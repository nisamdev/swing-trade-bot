"""The tests that matter: does the simulation cheat?

Each one pins down a place where a naive backtester quietly makes money it
could never have made in reality.
"""

from datetime import date, timedelta

import pytest

from app.backtest import run_backtest
from app.models import Bar, Money, Rules


def series(prices, symbol="TEST", start=date(2023, 1, 2), spread=1.0, volume=1_000_000):
    """Daily bars from a list of closes. Each day opens at the previous close."""
    bars = []
    day = start
    prev = prices[0]
    for close in prices:
        bars.append(
            Bar(
                symbol=symbol,
                day=day,
                open=prev,
                high=max(prev, close) + spread,
                low=min(prev, close) - spread,
                close=close,
                volume=volume,
            )
        )
        prev = close
        day += timedelta(days=1)
        while day.weekday() >= 5:  # keep it to weekdays, like a real market
            day += timedelta(days=1)
    return bars


def dip_then_recover(n=400):
    """A long uptrend with a sharp, recoverable dip near the end."""
    prices = [100 + i * 0.35 for i in range(n - 40)]
    peak = prices[-1]
    prices += [peak - i * 1.6 for i in range(1, 16)]   # the dip
    prices += [prices[-1] + i * 1.9 for i in range(1, 26)]  # the bounce
    return prices


FREE = Money(commission_per_trade=0.0, commission_per_share=0.0, slippage_bps=0.0)


def test_no_trades_when_history_is_too_short():
    bars = {"TEST": series([100.0] * 30)}
    with pytest.raises(ValueError):
        run_backtest(bars, Rules(), FREE, trade_from=date(2099, 1, 1))


def test_a_flat_market_produces_no_trades_and_loses_nothing():
    bars = {"TEST": series([100.0] * 300, spread=0.5)}
    out = run_backtest(bars, Rules(strategy="buy_the_dip"), FREE)
    assert out["stats"]["trades"] == 0
    assert out["stats"]["final_value"] == pytest.approx(10_000, abs=1)
    assert out["verdict"]["grade"] == "no-trades"


def test_entries_fill_at_the_next_open_not_the_signal_close():
    """The core anti-cheat rule. An entry price equal to a signal-day close
    would mean trading on information that arrived after the fact."""
    bars = {"TEST": series(dip_then_recover())}
    out = run_backtest(bars, Rules(strategy="buy_the_dip"), FREE)
    assert out["stats"]["trades"] >= 1

    rows = {b.day: b for b in bars["TEST"]}
    for trade in out["trades"]:
        entry_day = date.fromisoformat(trade["entry_day"])
        assert trade["entry_price"] == pytest.approx(rows[entry_day].open, abs=0.01)


def test_a_gap_below_the_stop_fills_at_the_open_not_the_stop_price():
    """The loss you actually take when a stock opens 20% lower."""
    prices = [100 + i * 0.4 for i in range(260)]
    bars = series(prices)
    # Slam one day far below the stop, opening at the low.
    crash_at = len(bars) - 3
    b = bars[crash_at]
    bars[crash_at] = Bar(b.symbol, b.day, 60.0, 62.0, 58.0, 59.0, b.volume)

    rules = Rules(strategy="breakout", volume_multiple=0, use_trend_filter=True)
    out = run_backtest({"TEST": bars}, rules, FREE)
    gapped = [t for t in out["trades"] if "Gapped" in (t["exit_reason"] or "")]
    if gapped:
        for trade in gapped:
            assert trade["exit_price"] < trade["stop"]


def test_stop_wins_when_a_single_day_contains_both_stop_and_target():
    """Daily bars cannot say which came first, so the unkind reading is used."""
    prices = [100 + i * 0.4 for i in range(200)]
    bars = series(prices)
    wild = len(bars) - 5
    b = bars[wild]
    # A day whose range swallows any plausible stop and target at once.
    bars[wild] = Bar(b.symbol, b.day, b.open, b.open * 1.5, b.open * 0.5, b.close, b.volume)

    rules = Rules(strategy="breakout", volume_multiple=0)
    out = run_backtest({"TEST": bars}, rules, FREE)
    hit_on_wild_day = [
        t for t in out["trades"] if t["exit_day"] == bars[wild].day.isoformat()
    ]
    for trade in hit_on_wild_day:
        assert "stop" in (trade["exit_reason"] or "").lower()


def test_it_never_spends_more_cash_than_it_has():
    bars = {
        f"S{i}": series(dip_then_recover(), symbol=f"S{i}")
        for i in range(6)
    }
    money = Money(starting_cash=2_000, max_open_positions=6, risk_percent=5,
                  max_position_percent=100, slippage_bps=0)
    out = run_backtest(bars, Rules(strategy="buy_the_dip"), money)
    for point in out["equity_curve"]:
        assert point["value"] >= 0


def test_position_limit_is_respected():
    bars = {
        f"S{i}": series(dip_then_recover(), symbol=f"S{i}")
        for i in range(5)
    }
    money = Money(starting_cash=200_000, max_open_positions=2, risk_percent=1)
    out = run_backtest(bars, Rules(strategy="buy_the_dip"), money)

    # Rebuild the holding periods and check no day held three at once.
    spans = [
        (date.fromisoformat(t["entry_day"]), date.fromisoformat(t["exit_day"]))
        for t in out["trades"]
    ]
    for probe, _ in spans:
        overlapping = sum(1 for a, b in spans if a <= probe <= b)
        assert overlapping <= 2


def test_costs_make_a_strategy_worse_never_better():
    bars = {"TEST": series(dip_then_recover())}
    rules = Rules(strategy="buy_the_dip")
    free = run_backtest(bars, rules, FREE)
    pricey = run_backtest(
        bars, rules,
        Money(commission_per_trade=1.0, commission_per_share=0.005, slippage_bps=10),
    )
    if free["stats"]["trades"]:
        assert pricey["stats"]["final_value"] <= free["stats"]["final_value"]


def test_risk_per_trade_caps_the_loss_on_a_stop_out():
    """A 1% risk setting should cost about 1% when the stop is hit."""
    bars = {"TEST": series(dip_then_recover())}
    money = Money(starting_cash=100_000, risk_percent=1.0, max_position_percent=100)
    out = run_backtest(bars, Rules(strategy="buy_the_dip"), money)

    stopped = [t for t in out["trades"] if "stop" in (t["exit_reason"] or "").lower()]
    for trade in stopped:
        # Allow headroom: later trades size off a changed account value.
        assert abs(trade["profit"]) < money.starting_cash * 0.03


def test_buy_and_hold_is_always_reported():
    bars = {"TEST": series(dip_then_recover())}
    out = run_backtest(bars, Rules(), FREE)
    assert "buy_and_hold" in out["stats"]
    assert isinstance(out["stats"]["beat_buy_and_hold_by"], float)


def test_every_trade_carries_a_human_readable_reason():
    bars = {"TEST": series(dip_then_recover())}
    out = run_backtest(bars, Rules(strategy="buy_the_dip"), FREE)
    for trade in out["trades"]:
        assert trade["reason"] and len(trade["reason"]) > 10
        assert trade["exit_reason"] and len(trade["exit_reason"]) > 10
