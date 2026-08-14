"""Three swing-trading ideas, written the way you would explain them out loud.

A strategy answers exactly two questions:

    "Would I buy this stock at today's close?"   -> entry()
    "Would I get out of this trade?"             -> exit()

It does not know about money, share counts, or brokers. The backtester and the
live bot own those, so the same strategy code decides both, and a backtest
therefore tests the thing that will actually trade.

Every reason string is written for a human to read in the app. If a rule blocks
a buy, the strategy says which rule and by how much -- "waiting" with no reason
is how a beginner loses trust in a bot.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from .indicators import (
    Series,
    atr_series,
    rolling_max,
    rsi_series,
    sma_series,
)
from .models import Bar, Rules, Verdict, SimTrade


@dataclass
class Chart:
    """Every number a strategy might need, precomputed once per stock."""

    bars: list[Bar]
    close: list[float]
    high: list[float]
    low: list[float]
    volume: list[float]
    trend: Series  # long-term average, the "is this stock healthy" line
    atr: Series
    rsi: Series
    fast: Series
    slow: Series
    highest: Series  # highest high over the breakout window
    vol_avg: Series
    rules: Rules

    def __len__(self) -> int:
        return len(self.bars)


def build_chart(bars: Sequence[Bar], rules: Rules) -> Chart:
    """Run every indicator once. Cheap enough that per-strategy pruning is not worth it."""
    close = [b.close for b in bars]
    high = [b.high for b in bars]
    low = [b.low for b in bars]
    volume = [b.volume for b in bars]

    return Chart(
        bars=list(bars),
        close=close,
        high=high,
        low=low,
        volume=volume,
        trend=sma_series(close, rules.trend_days),
        atr=atr_series(high, low, close, rules.atr_days),
        rsi=rsi_series(close, rules.rsi_days),
        fast=sma_series(close, rules.fast_days),
        slow=sma_series(close, rules.slow_days),
        highest=rolling_max(high, rules.breakout_days),
        vol_avg=sma_series(volume, 50),
        rules=rules,
    )


# --------------------------------------------------------------------------- #
# The shared safety filter
# --------------------------------------------------------------------------- #


def _trend_check(c: Chart, i: int) -> str | None:
    """Return a blocking reason, or None if the stock is in an uptrend.

    Buying a dip in a falling stock is the single most expensive beginner
    mistake, so this filter sits in front of every strategy.
    """
    if not c.rules.use_trend_filter:
        return None
    line = c.trend[i]
    if line is None:
        return (
            f"Warming up — needs {c.rules.trend_days} days of prices "
            f"before it can judge the trend"
        )
    if c.close[i] < line:
        gap = (line - c.close[i]) / line * 100
        return (
            f"Skipped: the stock is in a downtrend "
            f"({gap:.1f}% below its {c.rules.trend_days}-day average)"
        )
    return None


# --------------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------------- #


class Strategy:
    key = "base"
    name = "Base"
    tagline = ""
    how_it_works: list[str] = []
    best_for = ""
    # Which Rules fields the settings screen should show for this strategy.
    uses: list[str] = []

    def entry(self, c: Chart, i: int) -> Verdict:
        raise NotImplementedError

    def exit(self, c: Chart, i: int, trade: SimTrade) -> str | None:
        """A reason to sell at tomorrow's open, or None to keep holding.

        Stops and targets are handled by the backtester and the broker, since
        those fire intraday. This is only for close-of-day decisions.
        """
        return None


class BuyTheDip(Strategy):
    key = "buy_the_dip"
    name = "Buy the dip"
    tagline = "Wait for a healthy stock to go on sale, then buy the bounce."
    best_for = "Steady, well-known stocks and index funds. The gentlest of the three."
    how_it_works = [
        "Check the stock is in a long-term uptrend — today's price is above its 200-day average price.",
        "Wait for a pullback: the RSI momentum gauge drops below 40, meaning sellers have been in charge for a while.",
        "Buy only once it starts turning back up — today closed higher than yesterday.",
        "Sell at a target 4× the stock's normal daily range above the entry, or cut the loss 2× below it.",
    ]
    uses = [
        "use_trend_filter",
        "trend_days",
        "rsi_days",
        "rsi_buy_below",
        "atr_days",
        "stop_atr",
        "target_atr",
        "trail_atr",
        "max_hold_days",
    ]

    def entry(self, c: Chart, i: int) -> Verdict:
        rsi = c.rsi[i]
        if rsi is None:
            return Verdict(False, "Warming up — not enough price history yet")

        blocked = _trend_check(c, i)
        if blocked:
            return Verdict(False, blocked, {"rsi": round(rsi, 1)})

        limit = c.rules.rsi_buy_below
        if rsi >= limit:
            return Verdict(
                False,
                f"No dip yet — momentum is {rsi:.0f}, and this strategy waits for under {limit:.0f}",
                {"rsi": round(rsi, 1)},
            )

        if i < 1 or c.close[i] <= c.close[i - 1]:
            return Verdict(
                False,
                f"On sale (momentum {rsi:.0f}) but still falling — waiting for a green day",
                {"rsi": round(rsi, 1)},
            )

        return Verdict(
            True,
            f"Uptrend intact, momentum dipped to {rsi:.0f}, and today closed up "
            f"{(c.close[i] / c.close[i - 1] - 1) * 100:.1f}%",
            {"rsi": round(rsi, 1)},
        )


class Breakout(Strategy):
    key = "breakout"
    name = "Breakout"
    tagline = "Buy a stock the day it pushes to a new high on heavy trading."
    best_for = "Fast movers and strong markets. More trades, more false starts."
    how_it_works = [
        "Check the stock is in a long-term uptrend — above its 200-day average price.",
        "Wait for today's close to beat the highest price of the last 20 days.",
        "Confirm real interest: today's trading volume is at least 1.5× its recent average.",
        "Sell at a target 4× the stock's normal daily range above the entry, or cut the loss 2× below it.",
    ]
    uses = [
        "use_trend_filter",
        "trend_days",
        "breakout_days",
        "volume_multiple",
        "atr_days",
        "stop_atr",
        "target_atr",
        "trail_atr",
        "max_hold_days",
    ]

    def entry(self, c: Chart, i: int) -> Verdict:
        if i < 1:
            return Verdict(False, "Warming up — not enough price history yet")

        # Yesterday's rolling high, so today's own high cannot beat itself.
        prior_high = c.highest[i - 1]
        if prior_high is None:
            return Verdict(
                False, f"Warming up — needs {c.rules.breakout_days} days of prices"
            )

        blocked = _trend_check(c, i)
        if blocked:
            return Verdict(False, blocked)

        if c.close[i] <= prior_high:
            short_by = (prior_high - c.close[i]) / prior_high * 100
            return Verdict(
                False,
                f"No breakout — {short_by:.1f}% below its {c.rules.breakout_days}-day high "
                f"of ${prior_high:,.2f}",
            )

        avg = c.vol_avg[i]
        if c.rules.volume_multiple > 0:
            if avg is None:
                return Verdict(False, "Warming up — needs 50 days of volume history")
            needed = avg * c.rules.volume_multiple
            if c.volume[i] < needed:
                return Verdict(
                    False,
                    f"Broke out, but on quiet trading — volume was "
                    f"{c.volume[i] / avg:.1f}× average and this strategy wants "
                    f"{c.rules.volume_multiple:.1f}×",
                )

        multiple = c.volume[i] / avg if avg else 0.0
        return Verdict(
            True,
            f"Broke above its {c.rules.breakout_days}-day high of ${prior_high:,.2f} "
            f"on {multiple:.1f}× normal volume",
            {"volume_multiple": round(multiple, 2)},
        )


class TrendChange(Strategy):
    key = "trend_change"
    name = "Trend change"
    tagline = "Buy when the short-term average crosses above the long-term one."
    best_for = "Patient, hands-off trading. Fewest trades, longest holds."
    how_it_works = [
        "Track two average prices: a fast 20-day one and a slow 50-day one.",
        "Buy on the day the fast average crosses up through the slow one — the classic sign a trend has turned.",
        "Sell when it crosses back down, hits the target, or hits the stop.",
        "This one trades rarely. Long stretches of no signal are normal, not a bug.",
    ]
    uses = [
        "use_trend_filter",
        "trend_days",
        "fast_days",
        "slow_days",
        "atr_days",
        "stop_atr",
        "target_atr",
        "trail_atr",
        "max_hold_days",
    ]

    def entry(self, c: Chart, i: int) -> Verdict:
        if i < 1:
            return Verdict(False, "Warming up — not enough price history yet")

        fast, slow = c.fast[i], c.slow[i]
        prev_fast, prev_slow = c.fast[i - 1], c.slow[i - 1]
        if None in (fast, slow, prev_fast, prev_slow):
            return Verdict(
                False, f"Warming up — needs {c.rules.slow_days} days of prices"
            )

        blocked = _trend_check(c, i)
        if blocked:
            return Verdict(False, blocked)

        crossed = prev_fast <= prev_slow and fast > slow
        if not crossed:
            if fast > slow:
                return Verdict(
                    False,
                    f"Already trending up — the crossover happened earlier, "
                    f"so there is nothing new to buy today",
                )
            gap = (slow - fast) / slow * 100
            return Verdict(
                False,
                f"No crossover — the {c.rules.fast_days}-day average is still "
                f"{gap:.1f}% below the {c.rules.slow_days}-day one",
            )

        return Verdict(
            True,
            f"The {c.rules.fast_days}-day average just crossed above the "
            f"{c.rules.slow_days}-day one — the trend has turned up",
        )

    def exit(self, c: Chart, i: int, trade: SimTrade) -> str | None:
        fast, slow = c.fast[i], c.slow[i]
        if fast is None or slow is None:
            return None
        if fast < slow:
            return "Trend turned back down — the fast average crossed below the slow one"
        return None


STRATEGIES: dict[str, Strategy] = {
    s.key: s for s in (BuyTheDip(), Breakout(), TrendChange())
}


def get_strategy(key: str) -> Strategy:
    try:
        return STRATEGIES[key]
    except KeyError:
        raise ValueError(
            f"Unknown strategy {key!r}. Pick one of: {', '.join(sorted(STRATEGIES))}"
        ) from None


# Labels and help text for the settings screen. Keeping them next to the rules
# they describe means a new knob cannot ship without an explanation.
SETTING_HELP: dict[str, dict] = {
    "use_trend_filter": {
        "label": "Only buy stocks in an uptrend",
        "help": "Blocks every buy while the stock trades below its long-term average. Leave this on.",
        "type": "bool",
    },
    "trend_days": {
        "label": "Uptrend measured over",
        "help": "How many days of prices define 'the long-term trend'. 200 is the standard.",
        "type": "int", "min": 20, "max": 300, "unit": "days",
    },
    "rsi_days": {
        "label": "Momentum gauge length",
        "help": "How many days the RSI momentum gauge looks back. 14 is the standard.",
        "type": "int", "min": 2, "max": 50, "unit": "days",
    },
    "rsi_buy_below": {
        "label": "Buy when momentum drops below",
        "help": "Lower means rarer, deeper dips. 30 is a hard sell-off, 40 is an ordinary pullback.",
        "type": "float", "min": 5, "max": 60, "step": 1,
    },
    "breakout_days": {
        "label": "New high measured over",
        "help": "The stock must close above its highest price of this many days.",
        "type": "int", "min": 5, "max": 120, "unit": "days",
    },
    "volume_multiple": {
        "label": "Volume must beat average by",
        "help": "How much busier than normal the day has to be. Set to 0 to ignore volume.",
        "type": "float", "min": 0, "max": 5, "step": 0.1, "unit": "×",
    },
    "fast_days": {
        "label": "Fast average",
        "help": "The short-term average price line.",
        "type": "int", "min": 3, "max": 100, "unit": "days",
    },
    "slow_days": {
        "label": "Slow average",
        "help": "The long-term average price line. Must be longer than the fast one.",
        "type": "int", "min": 5, "max": 250, "unit": "days",
    },
    "atr_days": {
        "label": "Normal daily range measured over",
        "help": "Used to size the stop and target to this particular stock's volatility.",
        "type": "int", "min": 2, "max": 50, "unit": "days",
    },
    "stop_atr": {
        "label": "Cut the loss at",
        "help": "How far below your entry to bail out, in multiples of the stock's normal daily range. Tighter means more small losses.",
        "type": "float", "min": 0.5, "max": 10, "step": 0.25, "unit": "× daily range",
    },
    "target_atr": {
        "label": "Take the profit at",
        "help": "How far above your entry to cash out. Should be bigger than the stop, or the winners can't pay for the losers.",
        "type": "float", "min": 0.5, "max": 20, "step": 0.25, "unit": "× daily range",
    },
    "trail_atr": {
        "label": "Trailing stop",
        "help": "Follows the price up and locks in gains. Set to 0 to turn it off.",
        "type": "float", "min": 0, "max": 10, "step": 0.25, "unit": "× daily range",
    },
    "max_hold_days": {
        "label": "Give up after",
        "help": "Sell a trade that has gone nowhere, so your money isn't stuck in a dud.",
        "type": "int", "min": 1, "max": 250, "unit": "days",
    },
}


def describe_strategies() -> list[dict]:
    """Everything the app needs to render the strategy picker."""
    out = []
    for s in STRATEGIES.values():
        out.append(
            {
                "key": s.key,
                "name": s.name,
                "tagline": s.tagline,
                "best_for": s.best_for,
                "how_it_works": s.how_it_works,
                "settings": [
                    {"key": k, **SETTING_HELP[k]} for k in s.uses if k in SETTING_HELP
                ],
            }
        )
    return out
