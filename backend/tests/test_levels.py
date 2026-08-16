"""Support, resistance, and supply/demand zones.

The test that matters most is the lookahead one. Everything else here is
mechanics; `test_a_pivot_cannot_be_used_before_it_is_confirmed` is what keeps
the whole idea honest, because a level you "knew about" before it formed makes
any backtest look like genius.
"""

from datetime import date, timedelta

from app.indicators import atr_series
from app.levels import Landscape, build_levels, build_zones, find_pivots
from app.models import Bar, Rules


def bars(shape, spread=0.4):
    """`shape` is a list of closes; each bar opens at the previous close."""
    out = []
    day = date(2023, 1, 2)
    prev = shape[0]
    for close in shape:
        out.append(
            Bar("T", day, prev, max(prev, close) + spread,
                min(prev, close) - spread, close, 1_000_000)
        )
        prev = close
        day += timedelta(days=1)
    return out


def zigzag(peaks, valley=100.0, leg=10):
    """Alternating runs up to each peak and back down to `valley`."""
    out = [valley]
    for peak in peaks:
        out += [out[-1] + (peak - out[-1]) * (k / leg) for k in range(1, leg + 1)]
        out += [out[-1] + (valley - out[-1]) * (k / leg) for k in range(1, leg + 1)]
    return out


# --------------------------------------------------------------------------- #
# Pivots
# --------------------------------------------------------------------------- #


def test_a_clear_peak_is_found_as_a_swing_high():
    shape = [100, 101, 102, 108, 102, 101, 100]
    pivots = find_pivots(bars(shape), reach=3)
    highs = [p for p in pivots if p.kind == "high"]
    assert highs and highs[0].index == 3


def test_a_pivot_cannot_be_used_before_it_is_confirmed():
    """You only learn a day was the top once the following days fail to beat
    it. Using it earlier is lookahead, and it is the bug that makes support
    and resistance look magical in a backtest."""
    shape = [100, 101, 102, 108, 102, 101, 100]
    for pivot in find_pivots(bars(shape), reach=3):
        assert pivot.confirmed_at == pivot.index + 3
        assert pivot.confirmed_at > pivot.index


def test_a_bigger_reach_finds_fewer_turns():
    shape = zigzag([110, 108, 112], leg=6)
    assert len(find_pivots(bars(shape), reach=2)) >= len(
        find_pivots(bars(shape), reach=6)
    )


# --------------------------------------------------------------------------- #
# Levels
# --------------------------------------------------------------------------- #


def test_turns_at_the_same_price_become_one_level_with_several_touches():
    rows = bars(zigzag([120, 120, 120]))
    levels = build_levels(find_pivots(rows, 3), tolerance_percent=1.0, min_touches=2)
    assert levels
    top = max(levels, key=lambda lv: lv.price)
    assert top.strength >= 3, "three turns at the same price is one level, not three"


def test_a_price_touched_once_is_not_a_level():
    rows = bars(zigzag([120]))
    levels = build_levels(find_pivots(rows, 3), tolerance_percent=0.5, min_touches=3)
    assert levels == []


def test_a_level_is_inactive_until_its_second_touch_is_confirmed():
    rows = bars(zigzag([120, 120]))
    levels = build_levels(find_pivots(rows, 3), tolerance_percent=1.0, min_touches=2)
    assert levels
    level = levels[0]
    assert not level.active_at(level.confirmed_at - 1, 2)
    assert level.active_at(level.confirmed_at, 2)


# --------------------------------------------------------------------------- #
# Zones
# --------------------------------------------------------------------------- #


def quiet_then_launch():
    """Forty flat days, then a violent run up — a textbook demand shelf."""
    return [100.0] * 40 + [100 + i * 4.0 for i in range(1, 12)] + [140.0] * 10


def test_a_quiet_patch_before_a_surge_becomes_a_demand_zone():
    rows = bars(quiet_then_launch(), spread=0.2)
    atr = atr_series([b.high for b in rows], [b.low for b in rows],
                     [b.close for b in rows], 14)
    zones = build_zones(rows, atr, impulse_atr=1.5, base_bars=3)
    demand = [z for z in zones if z.kind == "demand"]
    assert demand, "a flat base followed by a 40% run should leave a zone"
    assert any(z.bottom <= 100.0 <= z.top for z in demand)


def test_a_zone_cannot_be_used_before_the_move_that_made_it():
    rows = bars(quiet_then_launch(), spread=0.2)
    atr = atr_series([b.high for b in rows], [b.low for b in rows],
                     [b.close for b in rows], 14)
    for zone in build_zones(rows, atr, impulse_atr=1.5, base_bars=3):
        assert zone.confirmed_at > zone.base_end
        assert not zone.fresh_at(zone.confirmed_at - 1, 1)


def test_a_zone_stops_being_fresh_once_price_returns():
    shape = quiet_then_launch() + [140 - i * 4.0 for i in range(1, 11)] + [100.0] * 5
    rows = bars(shape, spread=0.2)
    atr = atr_series([b.high for b in rows], [b.low for b in rows],
                     [b.close for b in rows], 14)
    zones = build_zones(rows, atr, impulse_atr=1.5, base_bars=3)
    revisited = [z for z in zones if z.kind == "demand" and z.tests]
    assert revisited, "price came all the way back, so the zone was tested"
    zone = revisited[0]
    last = len(rows) - 1
    assert zone.tests_before(last) >= 1
    assert not zone.fresh_at(last, 0)


def test_a_wild_base_is_not_a_shelf():
    """A shelf is a *quiet* patch. Chaos before a move is just more chaos."""
    shape = [100, 118, 88, 121, 85] + [100 + i * 5.0 for i in range(1, 12)]
    rows = bars(shape * 3, spread=1.0)
    atr = atr_series([b.high for b in rows], [b.low for b in rows],
                     [b.close for b in rows], 14)
    zones = build_zones(rows, atr, impulse_atr=1.5, base_bars=3, base_atr=0.8)
    for zone in zones:
        assert zone.height <= 100, "a shelf spanning the whole chart is not a shelf"


# --------------------------------------------------------------------------- #
# Asking the landscape questions
# --------------------------------------------------------------------------- #


def test_the_nearest_ceiling_and_floor_are_on_the_right_sides():
    rows = bars(zigzag([130, 130, 130], valley=100, leg=8))
    atr = atr_series([b.high for b in rows], [b.low for b in rows],
                     [b.close for b in rows], 14)
    land = Landscape(rows, atr, pivot_reach=3, level_tolerance_percent=1.5)

    i = len(rows) - 1
    price = 115.0
    above = land.resistance_above(i, price)
    below = land.support_below(i, price)
    if above:
        assert above.price > price
    if below:
        assert below.price < price


def test_nothing_is_reported_on_a_chart_with_no_turns():
    rows = bars([100.0] * 120)
    atr = atr_series([b.high for b in rows], [b.low for b in rows],
                     [b.close for b in rows], 14)
    land = Landscape(rows, atr)
    i = len(rows) - 1
    assert land.supply_above(i, 100.0) is None
    assert land.demand_below(i, 100.0) is None
