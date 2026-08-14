"""Chart maths, as plain functions over lists of numbers.

Everything returns a *series* the same length as its input, with ``None`` in
the leading slots where there is not enough history yet. Aligning the output to
the input this way means a strategy can ask "what was the 200-day average on
day 137?" by indexing, instead of re-slicing and recomputing -- which is what
turns a multi-year backtest from slow into instant.

``None`` always means "not enough data", never zero. Treating it as zero is how
a backtest ends up buying on day one against an average that does not exist.
"""

from collections.abc import Sequence

Series = list[float | None]


def sma_series(values: Sequence[float], days: int) -> Series:
    """Simple moving average -- the plain average of the last `days` closes."""
    if days <= 0:
        raise ValueError("days must be > 0")
    out: Series = [None] * len(values)
    if len(values) < days:
        return out

    window = sum(values[:days])
    out[days - 1] = window / days
    for i in range(days, len(values)):
        window += values[i] - values[i - days]
        out[i] = window / days
    return out


def ema_series(values: Sequence[float], days: int) -> Series:
    """Exponential moving average -- like an average, but recent days count more."""
    if days <= 0:
        raise ValueError("days must be > 0")
    out: Series = [None] * len(values)
    if len(values) < days:
        return out

    k = 2.0 / (days + 1)
    current = sum(values[:days]) / days  # seed with the SMA, the usual convention
    out[days - 1] = current
    for i in range(days, len(values)):
        current = values[i] * k + current * (1 - k)
        out[i] = current
    return out


def rsi_series(closes: Sequence[float], days: int = 14) -> Series:
    """Relative Strength Index (Wilder), 0-100.

    Below 30 is the textbook "oversold" line, above 70 "overbought". It is a
    speed-of-move gauge, not a prediction.
    """
    if days < 2:
        raise ValueError("days must be >= 2")
    out: Series = [None] * len(closes)
    if len(closes) < days + 1:
        return out

    gains: list[float] = []
    losses: list[float] = []
    for prev, curr in zip(closes, closes[1:]):
        delta = curr - prev
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    def value(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

    avg_gain = sum(gains[:days]) / days
    avg_loss = sum(losses[:days]) / days
    # gains[i] spans closes[i] -> closes[i+1], so the seed lands on closes[days].
    out[days] = value(avg_gain, avg_loss)

    for i in range(days, len(gains)):
        avg_gain = (avg_gain * (days - 1) + gains[i]) / days
        avg_loss = (avg_loss * (days - 1) + losses[i]) / days
        out[i + 1] = value(avg_gain, avg_loss)
    return out


def atr_series(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    days: int = 14,
) -> Series:
    """Average True Range -- how much this stock typically moves in a day, in dollars.

    Used to place stops. A stock with a $4 ATR needs a wider stop than one with
    a $0.40 ATR, or you get shaken out by ordinary noise.
    """
    n = min(len(highs), len(lows), len(closes))
    out: Series = [None] * n
    if n < days + 1:
        return out

    trs: list[float] = [0.0]  # index 0 has no previous close
    for i in range(1, n):
        trs.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )

    current = sum(trs[1 : days + 1]) / days
    out[days] = current
    for i in range(days + 1, n):
        current = (current * (days - 1) + trs[i]) / days
        out[i] = current
    return out


def rolling_max(values: Sequence[float], days: int) -> Series:
    """Highest value over the last `days` entries, including the current one."""
    return _rolling(values, days, max)


def rolling_min(values: Sequence[float], days: int) -> Series:
    """Lowest value over the last `days` entries, including the current one."""
    return _rolling(values, days, min)


def _rolling(values: Sequence[float], days: int, pick) -> Series:
    if days <= 0:
        raise ValueError("days must be > 0")
    out: Series = [None] * len(values)
    for i in range(days - 1, len(values)):
        out[i] = pick(values[i - days + 1 : i + 1])
    return out


def pct_change(a: float, b: float) -> float:
    """Percent move from `a` to `b`. Zero when `a` is zero, not a crash."""
    if not a:
        return 0.0
    return (b - a) / a * 100.0
