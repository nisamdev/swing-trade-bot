"""The bot itself: one decision round per trading day.

Swing trading is a once-a-day job, so this is a once-a-day loop. Every morning
shortly after the opening bell it:

  1. reads the account and the positions it already holds,
  2. sells anything that has run out of time,
  3. asks the strategy about each stock on the watchlist, using *yesterday's*
     finished daily bar,
  4. buys the ones that pass, with a stop and a target attached.

Using yesterday's finished bar and buying this morning is the same sequence the
backtester simulates. That is the whole reason the two agree: a bot that acts
on a half-formed bar is testing something the backtest never measured.

Nothing is bought while "Suggest only" is on. That is the default, and it is
the right way to spend the first few weeks.
"""

import asyncio
import logging
import uuid
from dataclasses import asdict
from datetime import datetime, time
from zoneinfo import ZoneInfo

from .market import Market, MarketError
from .models import Money, Rules, buy_price
from .scanner import scan
from .store import Store
from .strategies import build_chart, get_strategy, plan_exits

log = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")

# How late the scheduled round may still fire. Miss the window -- the app was
# closed all morning, say -- and it waits for tomorrow rather than buying at a
# time of day the strategy was never tested at.
RUN_WINDOW_MINUTES = 90

# The scanner is read-only, so a late run is harmless -- but a 'midday' scan at
# 3pm is not midday, and the setups it reports would be stale by the close.
SCAN_WINDOW_MINUTES = 150

DEFAULT_CONFIG = {
    "watchlist": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "GOOGL"],
    "rules": asdict(Rules()),
    "money": asdict(Money()),
    # Off by default: the bot suggests trades and places none until you say so.
    "auto_trade": False,
    # Local New York time to run the daily round. Ten minutes after the open
    # lets the first prints settle without drifting far from the open price.
    "run_at": "09:40",
    # The scanner sweeps a wide list of stocks instead of just the watchlist.
    # Midday leaves a few hours to look at what it found before the close.
    "scan_at": "12:30",
    "scan_enabled": True,
    "scan_universe_size": 60,
}


class Engine:
    def __init__(self, market: Market | None, store: Store) -> None:
        self.market = market
        self.store = store
        self._task: asyncio.Task | None = None
        self._busy = asyncio.Lock()
        self.last_check: str | None = None
        self.last_result: dict | None = None
        self.last_scan: str | None = None
        self.last_ideas: dict | None = None

    # ------------------------------------------------------------------ #
    # Config
    # ------------------------------------------------------------------ #

    async def config(self) -> dict:
        saved = await self.store.get_setting("config") or {}
        merged = {**DEFAULT_CONFIG, **saved}
        merged["rules"] = {**DEFAULT_CONFIG["rules"], **(saved.get("rules") or {})}
        merged["money"] = {**DEFAULT_CONFIG["money"], **(saved.get("money") or {})}
        return merged

    async def save_config(self, patch: dict) -> dict:
        current = await self.config()
        for key, value in patch.items():
            if key in ("rules", "money") and isinstance(value, dict):
                current[key] = {**current[key], **value}
            elif key == "watchlist":
                current[key] = _clean_watchlist(value)
            else:
                current[key] = value
        await self.store.set_setting("config", current)
        return current

    async def rules(self) -> Rules:
        return Rules(**(await self.config())["rules"])

    async def money(self) -> Money:
        return Money(**(await self.config())["money"])

    # ------------------------------------------------------------------ #
    # Background loop
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="swing-daily-loop")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        """Wake every minute; do real work once a day. Deliberately dull."""
        while True:
            try:
                await self._maybe_run()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Daily round failed; will try again next minute")
            await asyncio.sleep(60)

    async def _maybe_run(self) -> None:
        if self.market is None:
            return
        await self._maybe_scan()
        cfg = await self.config()
        now = datetime.now(NEW_YORK)
        run_at = _parse_time(cfg.get("run_at", "09:40"))

        already_ran_today = (
            self.last_check is not None
            and datetime.fromisoformat(self.last_check).astimezone(NEW_YORK).date()
            == now.date()
        )
        if already_ran_today or now.time() < run_at:
            return

        # Only inside a window after the scheduled time. Starting the app at
        # 15:30 must not fire a round that buys near the close: the backtest
        # measured entries at the open, and a live bot that ignores that is
        # trading something nobody tested.
        minutes_late = (
            now.hour * 60 + now.minute - (run_at.hour * 60 + run_at.minute)
        )
        if minutes_late > RUN_WINDOW_MINUTES:
            return

        status = await self.market.market_status()
        if not status["open"]:
            return

        await self.run_round(triggered_by="schedule")

    # ------------------------------------------------------------------ #
    # The daily round
    # ------------------------------------------------------------------ #

    async def run_round(self, triggered_by: str = "you") -> dict:
        """Look at everything once and act. Safe to call by hand at any time."""
        if self.market is None:
            raise MarketError("Alpaca is not connected — check your keys in .env.")

        async with self._busy:
            cfg = await self.config()
            rules = Rules(**cfg["rules"])
            money = Money(**cfg["money"])
            auto = bool(cfg["auto_trade"])
            watchlist = _clean_watchlist(cfg["watchlist"])

            await self.store.log(
                f"Daily round started ({triggered_by}). "
                f"{'Trading is on' if auto else 'Suggest only — nothing will be bought'}."
            )

            account = await self.market.account()
            positions = {p["symbol"]: p for p in await self.market.positions()}
            pending = await self.market.pending_buy_symbols()
            clock = await self.market.market_status()

            # A market order placed while the market is shut does not sit
            # harmlessly -- it queues and fills at whatever the next open
            # happens to be. So the round still evaluates and reports, but it
            # never sends an order into a closed market.
            may_order = auto and clock["open"]
            if auto and not clock["open"]:
                await self.store.log(
                    "Market is closed, so no orders were placed. Signals below "
                    "are what the bot would buy once it reopens.",
                    "warning",
                )

            sold = await self._sell_stale(positions, pending, rules, may_order)
            suggestions, considered = await self._look_for_buys(
                watchlist, positions, pending, rules, money, account, may_order
            )

            result = {
                "at": datetime.now(NEW_YORK).isoformat(timespec="seconds"),
                "triggered_by": triggered_by,
                "auto_trade": auto,
                "market_open": clock["open"],
                "account": account,
                "strategy": get_strategy(rules.strategy).name,
                "sold": sold,
                "buys": suggestions,
                "considered": considered,
            }
            self.last_check = result["at"]
            self.last_result = result

            bought = sum(1 for s in suggestions if s["placed"])
            await self.store.log(
                f"Daily round finished: {len(suggestions)} buy signal(s), "
                f"{bought} order(s) placed, {len(sold)} position(s) sold."
            )
            return result

    async def _sell_stale(
        self, positions: dict[str, dict], pending: set[str], rules: Rules, auto: bool
    ) -> list[dict]:
        """Close positions that have run out of time.

        Stops and targets already sit at the broker as part of each bracket
        order, so they fire whether or not this app is running. Only the
        "give up after N days" rule needs us awake.
        """
        sold: list[dict] = []
        entries = await self.store.open_journal_entries()
        for entry in entries:
            symbol = entry["symbol"]
            if symbol not in positions:
                # Not a position -- but an order that has not filled yet is
                # also not a position. Closing the journal row here would lose
                # the trade before it even started, and with it the time stop.
                if symbol in pending:
                    continue
                await self.store.record_close(symbol, "Closed at the broker")
                continue

            opened = datetime.fromisoformat(entry["at"])
            held = (datetime.now(opened.tzinfo) - opened).days
            if held < rules.max_hold_days:
                continue

            reason = (
                f"Held {held} days without hitting the target — "
                f"selling to free the money up"
            )
            if auto:
                try:
                    await self.market.sell_everything_in(symbol)
                    await self.store.record_close(symbol, reason)
                    await self.store.log(f"Sold {symbol}. {reason}", "action")
                except MarketError as exc:
                    await self.store.log(f"Could not sell {symbol}: {exc}", "error")
                    continue
            else:
                await self.store.log(
                    f"{symbol} is due to be sold ({reason}), but trading is off.",
                    "suggestion",
                )
            sold.append({"symbol": symbol, "reason": reason, "placed": auto,
                         "held_days": held})
        return sold

    async def _look_for_buys(
        self,
        watchlist: list[str],
        positions: dict[str, dict],
        pending: set[str],
        rules: Rules,
        money: Money,
        account: dict,
        auto: bool,
    ) -> tuple[list[dict], list[dict]]:
        strategy = get_strategy(rules.strategy)
        history = await self.market.history_for_signals(watchlist)

        # Unfilled buy orders count against the limit. They are money already
        # committed, even though the broker does not call them positions yet.
        committed = set(positions) | pending
        room = money.max_open_positions - len(committed)
        suggestions: list[dict] = []
        considered: list[dict] = []

        for symbol in watchlist:
            bars = history.get(symbol) or []
            if len(bars) < 2:
                considered.append(
                    {"symbol": symbol, "buy": False,
                     "reason": "No price history came back for this symbol"}
                )
                continue

            if symbol in positions:
                considered.append(
                    {"symbol": symbol, "buy": False,
                     "reason": "Already holding this one", "price": bars[-1].close}
                )
                continue

            if symbol in pending:
                considered.append(
                    {"symbol": symbol, "buy": False,
                     "reason": "An order for this one is already waiting to fill",
                     "price": bars[-1].close}
                )
                continue

            chart = build_chart(bars, rules)
            # The last finished daily bar. During market hours the newest bar
            # is still forming, so acting on it would mean deciding on a price
            # that has not settled -- and the backtest never did that.
            i = len(bars) - 1
            if _is_today(bars[-1].day) and len(bars) >= 2:
                i = len(bars) - 2

            verdict = strategy.entry(chart, i)
            atr = chart.atr[i] or 0.0
            row = {
                "symbol": symbol,
                "buy": verdict.buy,
                "reason": verdict.reason,
                "price": round(chart.close[i], 2),
                "as_of": chart.bars[i].day.isoformat(),
            }
            considered.append(row)

            if not verdict.buy or atr <= 0:
                continue

            plan = _size_it(chart, i, rules, money, account)
            if plan is None:
                await self.store.log(
                    f"{symbol} signalled a buy but the account is too small "
                    f"for even one share at this risk setting.",
                    "warning",
                )
                continue

            suggestion = {
                "symbol": symbol,
                "reason": verdict.reason,
                **plan,
                "placed": False,
                "order_id": None,
                "error": None,
            }

            if auto and room > 0:
                try:
                    order = await self.market.buy_with_safety_net(
                        symbol,
                        plan["shares"],
                        plan["stop"],
                        plan["target"],
                        tag=f"swing-{uuid.uuid4().hex[:12]}",
                    )
                    suggestion["placed"] = True
                    suggestion["order_id"] = order["id"]
                    room -= 1
                    await self.store.record_buy(
                        symbol, rules.strategy, plan["shares"], plan["stop"],
                        plan["target"], verdict.reason, order["id"],
                    )
                    await self.store.log(
                        f"Bought {plan['shares']} {symbol} at about "
                        f"${plan['entry']:,.2f}. Stop ${plan['stop']:,.2f}, "
                        f"target ${plan['target']:,.2f}. {verdict.reason}",
                        "action",
                    )
                except MarketError as exc:
                    suggestion["error"] = str(exc)
                    await self.store.log(f"Buy failed for {symbol}: {exc}", "error")
            elif auto:
                suggestion["error"] = (
                    f"Holding the maximum {money.max_open_positions} positions already"
                )
            else:
                await self.store.log(
                    f"{symbol} is a buy: {verdict.reason}. Trading is off, "
                    f"so nothing was ordered.",
                    "suggestion",
                )

            suggestions.append(suggestion)

        return suggestions, considered

    # ------------------------------------------------------------------ #
    # The scanner
    # ------------------------------------------------------------------ #

    async def _maybe_scan(self) -> None:
        cfg = await self.config()
        if not cfg.get("scan_enabled", True):
            return

        now = datetime.now(NEW_YORK)
        scan_at = _parse_time(cfg.get("scan_at", "12:30"))
        already = (
            self.last_scan is not None
            and datetime.fromisoformat(self.last_scan).astimezone(NEW_YORK).date()
            == now.date()
        )
        if already or now.time() < scan_at:
            return

        minutes_late = now.hour * 60 + now.minute - (scan_at.hour * 60 + scan_at.minute)
        if minutes_late > SCAN_WINDOW_MINUTES:
            return

        status = await self.market.market_status()
        if not status["open"]:
            return

        await self.run_scan(triggered_by="the midday schedule")

    async def run_scan(self, triggered_by: str = "you") -> dict:
        """Sweep the universe and rank what is set up. Never places an order.

        Scanning is deliberately read-only. A wide sweep finds far more
        candidates than a watchlist does, and letting it trade automatically
        would turn a research tool into a machine for buying whatever moved.
        """
        if self.market is None:
            raise MarketError("Alpaca is not connected — check your keys in .env.")

        cfg = await self.config()
        rules = Rules(**cfg["rules"])
        money = Money(**cfg["money"])
        account = await self.market.account()

        await self.store.log(f"Scan started ({triggered_by}).")
        result = await scan(
            self.market,
            _clean_watchlist(cfg["watchlist"]),
            rules,
            money,
            account,
            limit=int(cfg.get("scan_universe_size", 60)),
        )
        result["triggered_by"] = triggered_by
        result["strategy"] = get_strategy(rules.strategy).name

        self.last_scan = datetime.now(NEW_YORK).isoformat(timespec="seconds")
        self.last_ideas = result
        await self.store.save_scan(result)
        await self.store.log(
            f"Scan finished: looked at {result['looked_at']} stocks, "
            f"found {len(result['ideas'])} worth a look."
        )
        return result

    # ------------------------------------------------------------------ #
    # Buying by hand
    # ------------------------------------------------------------------ #

    async def buy_by_hand(self, symbol: str, shares: int | None = None) -> dict:
        """Open a position because a person asked, not because a rule fired.

        Sized and protected exactly like a strategy trade -- same risk percent,
        same ATR-based stop and target, same bracket order at the broker. The
        only difference is the reason recorded in the journal, which is kept
        distinct so a hand-picked trade never gets counted as evidence that the
        strategy works.

        The "Suggest only" switch does not block this. That switch governs
        whether the *bot* may act on its own; this is you acting.
        """
        if self.market is None:
            raise MarketError("Alpaca is not connected — check your keys in .env.")

        symbol = symbol.strip().upper()
        cfg = await self.config()
        rules = Rules(**cfg["rules"])
        money = Money(**cfg["money"])

        # A market order sent into a closed market queues and fills at whatever
        # the next open turns out to be -- with no price protection, and often
        # far from the price shown when the button was pressed.
        clock = await self.market.market_status()
        if not clock["open"]:
            when = clock["next_open"]
            raise MarketError(
                "The market is closed. An order placed now would sit until it "
                "reopens and fill at an unknown price"
                + (f" — try again after {when[:16].replace('T', ' ')}." if when else ".")
            )

        account = await self.market.account()
        positions = {p["symbol"] for p in await self.market.positions()}
        pending = await self.market.pending_buy_symbols()

        if symbol in positions:
            raise MarketError(
                f"You already hold {symbol}. Sell it first, or pick another stock."
            )
        if symbol in pending:
            raise MarketError(
                f"You already have an order for {symbol} waiting to fill. "
                f"Cancel it first if you want to change the size."
            )

        committed = positions | pending
        if len(committed) >= money.max_open_positions:
            raise MarketError(
                f"You already have {len(committed)} positions or waiting orders, "
                f"which is your limit of {money.max_open_positions}. Raise the "
                f"limit on the Strategy page, or sell something first."
            )

        history = await self.market.history_for_signals([symbol])
        bars = history.get(symbol) or []
        if len(bars) < rules.atr_days + 2:
            raise MarketError(
                f"Not enough price history for {symbol} to work out a sensible "
                f"stop. Check the symbol is a US-listed stock."
            )

        chart = build_chart(bars, rules)
        i = len(bars) - 1
        if _is_today(bars[-1].day) and len(bars) >= 2:
            i = len(bars) - 2
        atr = chart.atr[i] or 0.0
        if atr <= 0:
            raise MarketError(f"Could not measure {symbol}'s daily range.")

        plan = _size_it(chart, i, rules, money, account)
        if plan is None:
            raise MarketError(
                f"Your account is too small to buy even one share of {symbol} at "
                f"a {money.risk_percent}% risk setting."
            )

        if shares is not None:
            if shares < 1:
                raise MarketError("Share count has to be at least 1.")
            if shares * plan["entry"] > account["buying_power"]:
                raise MarketError(
                    f"{shares} shares would cost about "
                    f"${shares * plan['entry']:,.0f}, more than your "
                    f"${account['buying_power']:,.0f} of buying power."
                )
            plan["shares"] = shares
            plan["cost"] = round(shares * plan["entry"], 2)
            plan["risking"] = round(shares * (plan["entry"] - plan["stop"]), 2)

        reason = (
            f"Bought by hand at about ${plan['entry']:,.2f}, with the usual "
            f"{rules.stop_atr:g}× stop and {rules.target_atr:g}× target attached"
        )
        order = await self.market.buy_with_safety_net(
            symbol, plan["shares"], plan["stop"], plan["target"],
            tag=f"hand-{uuid.uuid4().hex[:12]}",
        )
        await self.store.record_buy(
            symbol, "by_hand", plan["shares"], plan["stop"], plan["target"],
            reason, order["id"],
        )
        await self.store.log(
            f"You bought {plan['shares']} {symbol} by hand at about "
            f"${plan['entry']:,.2f}. Stop ${plan['stop']:,.2f}, "
            f"target ${plan['target']:,.2f}, risking ${plan['risking']:,.0f}.",
            "action",
        )
        return {"symbol": symbol, "reason": reason, "order_id": order["id"], **plan}

    async def preview_buy(self, symbol: str) -> dict:
        """What a hand buy would cost and risk, without placing anything."""
        if self.market is None:
            raise MarketError("Alpaca is not connected — check your keys in .env.")

        symbol = symbol.strip().upper()
        cfg = await self.config()
        rules = Rules(**cfg["rules"])
        money = Money(**cfg["money"])

        account = await self.market.account()
        history = await self.market.history_for_signals([symbol])
        bars = history.get(symbol) or []
        if len(bars) < rules.atr_days + 2:
            raise MarketError(f"No usable price history for {symbol}.")

        chart = build_chart(bars, rules)
        i = len(bars) - 1
        if _is_today(bars[-1].day) and len(bars) >= 2:
            i = len(bars) - 2
        atr = chart.atr[i] or 0.0
        plan = _size_it(chart, i, rules, money, account)
        if plan is None:
            raise MarketError(
                f"Your account is too small to buy one share of {symbol} at a "
                f"{money.risk_percent}% risk setting."
            )

        verdict = get_strategy(rules.strategy).entry(chart, i)
        clock = await self.market.market_status()
        return {
            "symbol": symbol,
            "price": round(chart.close[i], 2),
            "as_of": chart.bars[i].day.isoformat(),
            "strategy_agrees": verdict.buy,
            "strategy_says": verdict.reason,
            "market_open": clock["open"],
            "next_open": clock["next_open"],
            **plan,
        }

    # ------------------------------------------------------------------ #
    # Read-only views
    # ------------------------------------------------------------------ #

    async def status(self) -> dict:
        cfg = await self.config()
        now = datetime.now(NEW_YORK)
        return {
            "connected": self.market is not None,
            "mode": "paper" if (self.market and self.market.paper) else "not connected",
            "auto_trade": cfg["auto_trade"],
            "strategy": get_strategy(cfg["rules"]["strategy"]).name,
            "watchlist": cfg["watchlist"],
            "run_at": cfg["run_at"],
            "last_check": self.last_check,
            "now_new_york": now.isoformat(timespec="seconds"),
            "loop_running": self._task is not None and not self._task.done(),
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _size_it(
    chart, i: int, rules: Rules, money: Money, account: dict
) -> dict | None:
    """Work out how many shares to buy, and where to get out.

    The share count comes from the risk, not the other way round: decide you
    are willing to lose 1% of the account, measure how far away the stop is,
    and divide. A wide stop therefore buys fewer shares, so every losing trade
    costs roughly the same regardless of which stock it was.

    The stop and target come from `plan_exits`, so the live bot places them at
    the same places the backtest assumed -- under a demand shelf, below the
    next ceiling -- rather than at a fixed distance the test never modelled.
    """
    price = chart.close[i]
    entry = buy_price(price, money)
    plan = plan_exits(chart, i, entry)
    stop, target = plan.stop, plan.target

    per_share_risk = entry - stop
    if per_share_risk <= 0 or target <= entry:
        return None

    reward_risk = (target - entry) / per_share_risk
    if reward_risk < rules.min_reward_risk:
        return None

    value = account["value"]
    shares = int((value * money.risk_percent / 100.0) // per_share_risk)
    shares = min(
        shares,
        int((value * money.max_position_percent / 100.0) // entry),
        int(account["buying_power"] // entry),
    )
    if shares < 1:
        return None

    return {
        "shares": shares,
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "stop_reason": plan.stop_reason,
        "target_reason": plan.target_reason,
        "reward_risk": round(reward_risk, 2),
        "cost": round(shares * entry, 2),
        "risking": round(shares * per_share_risk, 2),
        "risking_percent": round(shares * per_share_risk / value * 100, 2) if value else 0,
    }


def _clean_watchlist(raw) -> list[str]:
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    seen: list[str] = []
    for item in raw or []:
        symbol = str(item).strip().upper()
        if symbol and symbol not in seen:
            seen.append(symbol)
    return seen[:40]


def _parse_time(text: str) -> time:
    try:
        hour, minute = text.split(":")
        return time(int(hour), int(minute))
    except Exception:
        return time(9, 40)


def _is_today(day) -> bool:
    return day == datetime.now(NEW_YORK).date()
