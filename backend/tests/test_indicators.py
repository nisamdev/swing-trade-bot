from app.indicators import (
    atr_series,
    ema_series,
    rolling_max,
    rolling_min,
    rsi_series,
    sma_series,
)


def test_sma_warms_up_then_matches_hand_maths():
    values = [1, 2, 3, 4, 5]
    out = sma_series(values, 3)
    assert out[:2] == [None, None]
    assert out[2] == 2.0  # (1+2+3)/3
    assert out[4] == 4.0  # (3+4+5)/3


def test_sma_is_none_when_history_is_too_short():
    assert sma_series([1, 2], 5) == [None, None]


def test_ema_reacts_to_a_jump_faster_than_sma():
    """On a steady ramp both lag equally; the difference shows on a shock."""
    values = [100.0] * 20 + [200.0]
    assert ema_series(values, 10)[-1] > sma_series(values, 10)[-1]


def test_rsi_pins_to_100_when_every_day_is_up():
    out = rsi_series([float(i) for i in range(1, 40)], 14)
    assert out[13] is None
    assert out[-1] == 100.0


def test_rsi_pins_to_zero_when_every_day_is_down():
    out = rsi_series([float(i) for i in range(40, 1, -1)], 14)
    assert out[-1] == 0.0


def test_rsi_sits_mid_range_on_a_choppy_series():
    closes = [100 + (2 if i % 2 else -2) for i in range(60)]
    value = rsi_series(closes, 14)[-1]
    assert 30 < value < 70


def test_atr_measures_the_typical_daily_range():
    n = 30
    highs = [102.0] * n
    lows = [98.0] * n
    closes = [100.0] * n
    out = atr_series(highs, lows, closes, 14)
    assert out[13] is None
    assert abs(out[-1] - 4.0) < 1e-9  # every day ranged exactly 4


def test_rolling_max_and_min_include_the_current_value():
    values = [5, 1, 9, 3, 7]
    assert rolling_max(values, 3) == [None, None, 9, 9, 9]
    assert rolling_min(values, 3) == [None, None, 1, 1, 3]
