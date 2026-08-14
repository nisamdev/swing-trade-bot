"""Guards around placing orders.

Each of these pins down a bug that actually happened on a live paper account:
orders queued into a closed market, a three-position limit that let six orders
through, and a journal that closed a trade before it had filled.
"""

from dataclasses import asdict
from datetime import date, timedelta

import pytest

from app.engine import Engine
from app.market import MarketError
from app.models import Bar, Money, Rules
from app.store import Store


class FakeMarket:
    """Just enough Alpaca to exercise the engine's decisions."""

    paper = True

    def __init__(self, *, open_market=True, positions=None, pending=None):
        self._open = open_market
        self._positions = positions or []
        self._pending = set(pending or ())
        self.orders_placed = []
        self.sold = []

    async def account(self):
        return {"mode": "paper", "value": 100_000.0, "cash": 100_000.0,
                "buying_power": 100_000.0, "day_change": 0.0}

    async def market_status(self):
        return {"open": self._open, "next_open": "2026-08-17T09:30:00-04:00",
                "next_close": None, "now": None}

    async def positions(self):
        return list(self._positions)

    async def pending_buy_symbols(self):
        return set(self._pending)

    async def history_for_signals(self, symbols, days=400):
        return {s: rising_bars(s) for s in symbols}

    async def buy_with_safety_net(self, symbol, shares, stop, target, tag=None):
        self.orders_placed.append(
            {"symbol": symbol, "shares": shares, "stop": stop, "target": target}
        )
        return {"id": f"order-{symbol}", "symbol": symbol, "shares": shares}

    async def sell_everything_in(self, symbol):
        self.sold.append(symbol)
        return {"id": f"sell-{symbol}"}


def rising_bars(symbol, n=320):
    """A steady uptrend that dips at the end — a "buy the dip" signal."""
    closes = [100 + i * 0.6 for i in range(n)]
    closes += [closes[-1] - i * 1.5 for i in range(1, 13)]
    closes.append(closes[-1] + 2.5)

    out = []
    day = date.today() - timedelta(days=len(closes) + 1)
    prev = closes[0]
    for close in closes:
        out.append(Bar(symbol, day, prev, max(prev, close) + 1,
                       min(prev, close) - 1, close, 1_000_000))
        prev = close
        day += timedelta(days=1)
    return out


@pytest.fixture
def engine(tmp_path):
    def build(market):
        return Engine(market, Store(tmp_path / "test.db"))
    return build


async def configure(eng, auto=True, **rules):
    await eng.save_config({
        "watchlist": ["AAA", "BBB", "CCC", "DDD"],
        "rules": {**asdict(Rules(strategy="buy_the_dip")), **rules},
        "money": asdict(Money(max_open_positions=3)),
        "auto_trade": auto,
    })


# --------------------------------------------------------------------------- #
# A closed market
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_daily_round_places_nothing_while_the_market_is_closed(engine):
    market = FakeMarket(open_market=False)
    eng = engine(market)
    await configure(eng)

    result = await eng.run_round()

    assert result["market_open"] is False
    assert market.orders_placed == [], "a closed market must not receive orders"
    # It still reports what it would have bought.
    assert any(row["buy"] for row in result["considered"])


@pytest.mark.asyncio
async def test_buying_by_hand_is_refused_while_the_market_is_closed(engine):
    eng = engine(FakeMarket(open_market=False))
    await configure(eng)

    with pytest.raises(MarketError, match="market is closed"):
        await eng.buy_by_hand("AAA")


@pytest.mark.asyncio
async def test_daily_round_does_place_orders_when_the_market_is_open(engine):
    market = FakeMarket(open_market=True)
    eng = engine(market)
    await configure(eng)

    await eng.run_round()
    assert market.orders_placed, "an open market with a signal should trade"


# --------------------------------------------------------------------------- #
# The position limit
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unfilled_orders_count_towards_the_position_limit(engine):
    """Three orders already waiting means the limit is reached, even though
    the broker reports no positions at all."""
    market = FakeMarket(open_market=True, pending=["XXX", "YYY", "ZZZ"])
    eng = engine(market)
    await configure(eng)

    await eng.run_round()
    assert market.orders_placed == []


@pytest.mark.asyncio
async def test_hand_buy_is_refused_once_the_limit_is_reached(engine):
    market = FakeMarket(open_market=True, pending=["XXX", "YYY", "ZZZ"])
    eng = engine(market)
    await configure(eng)

    with pytest.raises(MarketError, match="limit"):
        await eng.buy_by_hand("AAA")


@pytest.mark.asyncio
async def test_a_symbol_with_a_waiting_order_is_not_bought_twice(engine):
    market = FakeMarket(open_market=True, pending=["AAA"])
    eng = engine(market)
    await configure(eng)

    with pytest.raises(MarketError, match="already have an order"):
        await eng.buy_by_hand("AAA")

    result = await eng.run_round()
    reasons = {r["symbol"]: r["reason"] for r in result["considered"]}
    assert "waiting to fill" in reasons["AAA"]
    assert not any(o["symbol"] == "AAA" for o in market.orders_placed)


@pytest.mark.asyncio
async def test_the_round_never_exceeds_the_position_limit(engine):
    market = FakeMarket(open_market=True)
    eng = engine(market)
    await configure(eng)

    await eng.run_round()
    assert len(market.orders_placed) <= 3


# --------------------------------------------------------------------------- #
# The journal
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_an_unfilled_order_is_not_written_off_as_closed(engine):
    """The bug: 'not a position' was read as 'closed at the broker', which
    threw away the trade before it existed — and with it the time stop."""
    market = FakeMarket(open_market=True, pending=["AAA"])
    eng = engine(market)
    # Suggest-only, so the round cannot muddy the journal with fresh buys.
    await configure(eng, auto=False)
    await eng.store.record_buy("AAA", "buy_the_dip", 10, 90.0, 120.0, "test", "o1")

    await eng.run_round()

    rows = await eng.store.open_journal_entries()
    assert [r["symbol"] for r in rows] == ["AAA"], "the trade should still be open"


@pytest.mark.asyncio
async def test_a_position_that_really_did_close_is_marked_closed(engine):
    market = FakeMarket(open_market=True)  # no position, no pending order
    eng = engine(market)
    await configure(eng, auto=False)
    await eng.store.record_buy("AAA", "buy_the_dip", 10, 90.0, 120.0, "test", "o1")

    await eng.run_round()

    rows = await eng.store.open_journal_entries()
    assert rows == []
