"""Each strategy's rules, checked one at a time.

Every "no" a strategy gives has to come with a reason a beginner can read, so
these tests assert on the wording as well as the decision.
"""

from datetime import date, timedelta

from app.models import Bar, Rules
from app.strategies import build_chart, describe_strategies, get_strategy


def bars_from(closes, volumes=None, spread=1.0):
    volumes = volumes or [1_000_000] * len(closes)
    out = []
    day = date(2022, 1, 3)
    prev = closes[0]
    for close, volume in zip(closes, volumes):
        out.append(
            Bar("T", day, prev, max(prev, close) + spread,
                min(prev, close) - spread, close, volume)
        )
        prev = close
        day += timedelta(days=1)
    return out


def uptrend(n=300, slope=0.3, base=100.0):
    return [base + i * slope for i in range(n)]


def downtrend(n=300, slope=0.3, base=200.0):
    return [base - i * slope for i in range(n)]


# --------------------------------------------------------------------------- #
# The shared trend filter
# --------------------------------------------------------------------------- #


def test_downtrend_is_blocked_and_says_so_in_plain_english():
    rules = Rules(strategy="buy_the_dip")
    chart = build_chart(bars_from(downtrend()), rules)
    verdict = get_strategy("buy_the_dip").entry(chart, len(chart) - 1)
    assert verdict.buy is False
    assert "downtrend" in verdict.reason.lower()


def test_trend_filter_can_be_turned_off():
    rules = Rules(strategy="buy_the_dip", use_trend_filter=False)
    chart = build_chart(bars_from(downtrend()), rules)
    verdict = get_strategy("buy_the_dip").entry(chart, len(chart) - 1)
    assert "downtrend" not in verdict.reason.lower()


def test_warming_up_is_reported_not_treated_as_a_no():
    rules = Rules()
    chart = build_chart(bars_from(uptrend(n=20)), rules)
    verdict = get_strategy("buy_the_dip").entry(chart, len(chart) - 1)
    assert verdict.buy is False
    assert "warming up" in verdict.reason.lower()


# --------------------------------------------------------------------------- #
# Buy the dip
# --------------------------------------------------------------------------- #


def test_buy_the_dip_waits_for_a_dip():
    rules = Rules(strategy="buy_the_dip")
    chart = build_chart(bars_from(uptrend()), rules)  # straight up, no dip
    verdict = get_strategy("buy_the_dip").entry(chart, len(chart) - 1)
    assert verdict.buy is False
    assert "no dip yet" in verdict.reason.lower()


def dipping_uptrend():
    """A strong uptrend with a pullback shallow enough to stay above the
    200-day average -- which is exactly the setup this strategy hunts for."""
    base = uptrend(300, slope=0.6)
    return base + [base[-1] - i * 1.5 for i in range(1, 13)]


def test_buy_the_dip_will_not_catch_a_falling_knife():
    """Dipped far enough, but still going down: it must wait for a green day."""
    rules = Rules(strategy="buy_the_dip")
    chart = build_chart(bars_from(dipping_uptrend()), rules)
    verdict = get_strategy("buy_the_dip").entry(chart, len(chart) - 1)
    assert verdict.buy is False
    assert "green day" in verdict.reason.lower()


def test_buy_the_dip_fires_on_the_first_green_day_after_a_dip():
    closes = dipping_uptrend()
    closes.append(closes[-1] + 2.5)  # the bounce day
    rules = Rules(strategy="buy_the_dip")
    chart = build_chart(bars_from(closes), rules)
    verdict = get_strategy("buy_the_dip").entry(chart, len(chart) - 1)
    assert verdict.buy is True
    assert "momentum dipped" in verdict.reason.lower()


# --------------------------------------------------------------------------- #
# Breakout
# --------------------------------------------------------------------------- #


def test_breakout_needs_a_new_high():
    closes = uptrend(250) + [uptrend(250)[-1] - 5] * 10  # drifting sideways/down
    rules = Rules(strategy="breakout")
    chart = build_chart(bars_from(closes), rules)
    verdict = get_strategy("breakout").entry(chart, len(chart) - 1)
    assert verdict.buy is False
    assert "no breakout" in verdict.reason.lower()


def test_breakout_rejects_a_quiet_day_even_at_a_new_high():
    closes = uptrend(250) + [uptrend(250)[-1] + 40]
    volumes = [1_000_000] * 250 + [100_000]  # a tenth of normal
    rules = Rules(strategy="breakout", volume_multiple=1.5)
    chart = build_chart(bars_from(closes, volumes), rules)
    verdict = get_strategy("breakout").entry(chart, len(chart) - 1)
    assert verdict.buy is False
    assert "quiet trading" in verdict.reason.lower()


def test_breakout_fires_on_a_new_high_with_heavy_volume():
    closes = uptrend(250) + [uptrend(250)[-1] + 40]
    volumes = [1_000_000] * 250 + [5_000_000]
    rules = Rules(strategy="breakout", volume_multiple=1.5)
    chart = build_chart(bars_from(closes, volumes), rules)
    verdict = get_strategy("breakout").entry(chart, len(chart) - 1)
    assert verdict.buy is True
    assert "broke above" in verdict.reason.lower()


def test_breakout_volume_rule_can_be_switched_off():
    closes = uptrend(250) + [uptrend(250)[-1] + 40]
    volumes = [1_000_000] * 250 + [1]
    rules = Rules(strategy="breakout", volume_multiple=0)
    chart = build_chart(bars_from(closes, volumes), rules)
    assert get_strategy("breakout").entry(chart, len(chart) - 1).buy is True


# --------------------------------------------------------------------------- #
# Trend change
# --------------------------------------------------------------------------- #


def test_trend_change_only_fires_on_the_crossover_day():
    # Falls long enough for the fast average to sit below the slow one, then
    # turns up hard so it crosses back through.
    closes = uptrend(260) + [uptrend(260)[-1] - i * 1.5 for i in range(1, 60)]
    closes += [closes[-1] + i * 4.0 for i in range(1, 45)]
    rules = Rules(strategy="trend_change", use_trend_filter=False)
    chart = build_chart(bars_from(closes), rules)
    strategy = get_strategy("trend_change")

    fired = [
        i for i in range(len(chart)) if strategy.entry(chart, i).buy
    ]
    assert len(fired) >= 1
    # The day after a crossover must not fire again.
    for i in fired:
        assert not strategy.entry(chart, i + 1).buy if i + 1 < len(chart) else True


def test_trend_change_explains_why_it_is_quiet():
    rules = Rules(strategy="trend_change", use_trend_filter=False)
    chart = build_chart(bars_from(uptrend()), rules)
    verdict = get_strategy("trend_change").entry(chart, len(chart) - 1)
    assert verdict.buy is False
    assert "already trending up" in verdict.reason.lower()


# --------------------------------------------------------------------------- #
# The catalogue the UI renders
# --------------------------------------------------------------------------- #


def test_every_strategy_is_fully_described():
    for entry in describe_strategies():
        assert entry["name"] and entry["tagline"] and entry["best_for"]
        assert len(entry["how_it_works"]) >= 3
        assert entry["settings"], "a strategy with no adjustable settings is a bug"
        for setting in entry["settings"]:
            assert setting["label"] and setting["help"]
