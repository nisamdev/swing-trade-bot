"""Everything that talks to Alpaca.

Swing trading needs far less from a broker than day trading does: daily price
bars, the account balance, and the ability to place one bracket order a day.
There is no websocket and no minute data here on purpose -- a strategy that
holds for weeks does not care what happened in the last thirty seconds, and
every live feed is one more thing that can break at 3am.

alpaca-py's clients are synchronous, so each call is pushed onto a worker
thread and awaited. That keeps a slow HTTP request from freezing the web app.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

from .models import Bar

log = logging.getLogger(__name__)


class MarketError(RuntimeError):
    """Something went wrong talking to the broker. The message is shown to the user."""


def round_price(price: float) -> float:
    """Alpaca rejects sub-penny prices on anything trading at $1 or more."""
    return round(price, 2) if price >= 1.0 else round(price, 4)


class Market:
    def __init__(self, api_key: str, secret_key: str, paper: bool, feed: str) -> None:
        if not api_key or not secret_key:
            raise MarketError(
                "No Alpaca keys found. Put ALPACA_API_KEY and ALPACA_SECRET_KEY "
                "in the .env file at the project root, then restart."
            )
        self.paper = paper
        try:
            self._feed = DataFeed(feed.lower())
        except ValueError:
            raise MarketError(
                f"ALPACA_DATA_FEED is {feed!r}, which is not a feed. "
                f"Use one of: {', '.join(f.value for f in DataFeed)}"
            ) from None

        self._trading = TradingClient(api_key, secret_key, paper=paper)
        self._data = StockHistoricalDataClient(api_key, secret_key)

    # ------------------------------------------------------------------ #
    # Account
    # ------------------------------------------------------------------ #

    async def account(self) -> dict:
        try:
            acct = await asyncio.to_thread(self._trading.get_account)
        except Exception as exc:
            raise MarketError(f"Could not read your Alpaca account: {exc}") from exc
        return {
            "mode": "paper" if self.paper else "LIVE",
            "value": float(acct.equity or 0),
            "cash": float(acct.cash or 0),
            "buying_power": float(acct.buying_power or 0),
            "day_change": float(acct.equity or 0) - float(acct.last_equity or 0),
        }

    async def market_status(self) -> dict:
        try:
            clock = await asyncio.to_thread(self._trading.get_clock)
        except Exception as exc:
            raise MarketError(f"Could not read the market clock: {exc}") from exc
        return {
            "open": bool(clock.is_open),
            "next_open": clock.next_open.isoformat() if clock.next_open else None,
            "next_close": clock.next_close.isoformat() if clock.next_close else None,
            "now": clock.timestamp.isoformat() if clock.timestamp else None,
        }

    async def positions(self) -> list[dict]:
        try:
            rows = await asyncio.to_thread(self._trading.get_all_positions)
        except Exception as exc:
            raise MarketError(f"Could not read your positions: {exc}") from exc
        return [
            {
                "symbol": p.symbol,
                "shares": int(float(p.qty)),
                "bought_at": float(p.avg_entry_price),
                "price_now": float(p.current_price) if p.current_price else None,
                "value": float(p.market_value or 0),
                "profit": float(p.unrealized_pl or 0),
                "profit_percent": float(p.unrealized_plpc or 0) * 100,
            }
            for p in rows
        ]

    # ------------------------------------------------------------------ #
    # Prices
    # ------------------------------------------------------------------ #

    async def daily_bars(
        self,
        symbols: list[str],
        start: date,
        end: date | None = None,
    ) -> dict[str, list[Bar]]:
        """Daily prices for each symbol, oldest first.

        Split- and dividend-adjusted, which matters enormously: an unadjusted
        4-for-1 split looks like a 75% crash and will trip every stop in a
        backtest for a reason that never happened.
        """
        symbols = [s.strip().upper() for s in symbols if s.strip()]
        if not symbols:
            return {}

        end = end or date.today()
        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,
            start=datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc),
            end=datetime.combine(end, datetime.max.time(), tzinfo=timezone.utc),
            adjustment=Adjustment.ALL,
            feed=self._feed,
        )
        try:
            barset = await asyncio.to_thread(self._data.get_stock_bars, request)
        except Exception as exc:
            raise MarketError(
                f"Could not download prices for {', '.join(symbols)}: {exc}"
            ) from exc

        raw = getattr(barset, "data", {}) or {}
        out: dict[str, list[Bar]] = {}
        for symbol in symbols:
            rows = raw.get(symbol) or []
            out[symbol] = [
                Bar(
                    symbol=symbol,
                    day=b.timestamp.date(),
                    open=float(b.open),
                    high=float(b.high),
                    low=float(b.low),
                    close=float(b.close),
                    volume=float(b.volume or 0),
                )
                for b in rows
            ]
        return out

    async def history_for_signals(self, symbols: list[str], days: int = 400) -> dict[str, list[Bar]]:
        """Enough recent history to compute a 200-day average and then some.

        Calendar days, not trading days -- roughly 1.45 calendar days per
        trading day, so 400 covers a 200-day average comfortably.
        """
        start = date.today() - timedelta(days=int(days * 1.6))
        return await self.daily_bars(symbols, start)

    # ------------------------------------------------------------------ #
    # Orders
    # ------------------------------------------------------------------ #

    async def buy_with_safety_net(
        self,
        symbol: str,
        shares: int,
        stop: float,
        target: float,
        tag: str | None = None,
    ) -> dict:
        """Buy at market with the stop and target attached in one order.

        This is a bracket order: the moment the buy fills, Alpaca places both
        the sell-at-a-profit and the sell-at-a-loss orders for you, and cancels
        one when the other fills. It means the trade is protected even if this
        app is switched off -- which for a strategy that holds for weeks is not
        a nice-to-have.

        Good-till-cancelled, because a swing trade outlives the trading day.
        """
        request = MarketOrderRequest(
            symbol=symbol,
            qty=shares,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round_price(target)),
            stop_loss=StopLossRequest(stop_price=round_price(stop)),
            client_order_id=tag,
        )
        try:
            order = await asyncio.to_thread(self._trading.submit_order, request)
        except Exception as exc:
            raise MarketError(f"Alpaca rejected the buy order for {symbol}: {exc}") from exc
        return _order_dict(order)

    async def sell_everything_in(self, symbol: str) -> dict | None:
        """Close a position at market. Returns None if we were already out.

        The stop and target legs are cancelled first: a resting stop reserves
        the shares, and leaving it in place makes the sell fail for
        "insufficient quantity" -- which is exactly how a position you thought
        you closed survives until morning.
        """
        await self.cancel_orders(symbol)
        try:
            order = await asyncio.to_thread(self._trading.close_position, symbol)
        except Exception as exc:
            text = str(exc).lower()
            if "position does not exist" in text or "404" in text:
                return None
            raise MarketError(f"Could not sell {symbol}: {exc}") from exc
        return _order_dict(order)

    async def cancel_orders(self, symbol: str | None = None) -> int:
        try:
            request = GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True)
            orders = await asyncio.to_thread(self._trading.get_orders, request)
        except Exception as exc:
            raise MarketError(f"Could not read your open orders: {exc}") from exc

        flat: list = []
        for order in orders:
            flat.append(order)
            flat.extend(getattr(order, "legs", None) or [])

        cancelled = 0
        for order in flat:
            if symbol and order.symbol != symbol:
                continue
            try:
                await asyncio.to_thread(self._trading.cancel_order_by_id, order.id)
                cancelled += 1
            except Exception as exc:
                log.warning("Could not cancel order %s: %s", order.id, exc)
        return cancelled

    async def pending_buy_symbols(self) -> set[str]:
        """Symbols with a buy order sitting unfilled at the broker.

        An order placed outside market hours stays 'accepted' until the next
        open, so it is not a position yet — but the money is spoken for.
        Treating those symbols as free is how you end up with six orders
        against a three-position limit.
        """
        try:
            request = GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True)
            orders = await asyncio.to_thread(self._trading.get_orders, request)
        except Exception as exc:
            raise MarketError(f"Could not read your open orders: {exc}") from exc

        out: set[str] = set()
        for order in orders:
            side = str(getattr(order.side, "value", order.side)).lower()
            if side == "buy":
                out.add(order.symbol)
        return out

    async def open_orders(self) -> list[dict]:
        try:
            request = GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True)
            orders = await asyncio.to_thread(self._trading.get_orders, request)
        except Exception as exc:
            raise MarketError(f"Could not read your open orders: {exc}") from exc
        return [_order_dict(o) for o in orders]


def _order_dict(order) -> dict:
    filled = getattr(order, "filled_avg_price", None)
    return {
        "id": str(order.id),
        "symbol": order.symbol,
        "side": str(getattr(order.side, "value", order.side)),
        "shares": int(float(order.qty or 0)),
        "status": str(getattr(order.status, "value", order.status)),
        "filled_shares": int(float(getattr(order, "filled_qty", 0) or 0)),
        "filled_price": float(filled) if filled else None,
        "submitted_at": (
            order.submitted_at.isoformat() if getattr(order, "submitted_at", None) else None
        ),
    }
