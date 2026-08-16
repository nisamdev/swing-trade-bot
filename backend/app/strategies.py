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
    ema_series,
    rolling_max,
    rsi_series,
    sma_series,
)
from .levels import Landscape
from .models import Bar, Rules, Verdict, SimTrade


@dataclass
class Chart:
    """Every number a strategy might need, precomputed once per stock."""

    bars: list[Bar]
    close: list[float]
    high: list[float]
    low: list[float]
    volume: list[float]
    trend: Series  # the 200-day line: "is this stock healthy?"
    ema_fast: Series  # the 8-day line: "is it still moving my way right now?"
    atr: Series
    rsi: Series
    fast: Series
    slow: Series
    highest: Series  # highest high over the breakout window
    vol_avg: Series
    rules: Rules
    # Support, resistance and supply/demand zones. Built lazily because a
    # backtest over a hundred symbols does not always need them.
    _landscape: Landscape | None = None

    def __len__(self) -> int:
        return len(self.bars)

    @property
    def landscape(self) -> Landscape:
        if self._landscape is None:
            self._landscape = Landscape(
                self.bars,
                self.atr,
                pivot_reach=self.rules.pivot_reach,
                level_tolerance_percent=self.rules.level_tolerance_percent,
                min_touches=self.rules.min_touches,
                zone_impulse_atr=self.rules.zone_impulse_atr,
                zone_base_bars=self.rules.zone_base_bars,
            )
        return self._landscape


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
        ema_fast=ema_series(close, rules.fast_ema_days),
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


class ZoneBounce(Strategy):
    key = "zone_bounce"
    name = "Bounce off a shelf"
    tagline = "Buy where buyers showed up in force last time, the first time price returns."
    best_for = (
        "Anyone who reads charts by hand. This is the closest to how a discretionary "
        "swing trader actually trades, with the judgement written down as rules."
    )
    how_it_works = [
        "Check the stock is in a long-term uptrend — above its 200-day average price.",
        "Find a demand shelf: a quiet patch of trading that price then shot away from, which means buyers were waiting there in size.",
        "Wait for price to come back down and touch that shelf — and only count it if price has not already been back several times, because a shelf gets eaten away each visit.",
        "Buy when it stops falling and closes back above the shelf, not while it is still dropping through it.",
        "Put the stop just under the shelf, and the target just under the next ceiling of sellers above. Skip the trade if that target is not worth the risk.",
    ]
    uses = [
        "use_trend_filter",
        "trend_days",
        "zone_impulse_atr",
        "zone_base_bars",
        "zone_max_tests",
        "pivot_reach",
        "min_reward_risk",
        "atr_days",
        "stop_atr",
        "target_atr",
        "max_hold_days",
    ]

    def entry(self, c: Chart, i: int) -> Verdict:
        if i < 1:
            return Verdict(False, "Warming up — not enough price history yet")
        if c.atr[i] is None:
            return Verdict(False, "Warming up — not enough price history yet")

        blocked = _trend_check(c, i)
        if blocked:
            return Verdict(False, blocked)

        land = c.landscape
        zone = land.standing_in(i, "demand", c.rules.zone_max_tests)
        if zone is None:
            near = land.demand_below(i, c.close[i], c.rules.zone_max_tests)
            if near is None:
                return Verdict(
                    False,
                    "No fresh demand shelf below — nothing here worth waiting for",
                )
            away = (c.close[i] - near.top) / c.close[i] * 100
            return Verdict(
                False,
                f"Waiting — the nearest demand shelf (${near.bottom:,.2f}–"
                f"${near.top:,.2f}) is {away:.1f}% below today's price",
            )

        # Do not buy while it is still cutting through the shelf.
        if c.close[i] < zone.top and c.close[i] <= c.close[i - 1]:
            return Verdict(
                False,
                f"In the demand shelf (${zone.bottom:,.2f}–${zone.top:,.2f}) but "
                f"still falling — waiting for it to turn back up",
            )

        visits = zone.tests_before(i)
        freshness = (
            "untouched since it formed" if visits == 0
            else f"back for visit {visits + 1}"
        )
        return Verdict(
            True,
            f"Price came back to a demand shelf at ${zone.bottom:,.2f}–"
            f"${zone.top:,.2f} ({freshness}, price left it "
            f"{zone.impulse_atr:.1f}× faster than a normal day) and closed back up",
            {"zone": zone.as_dict()},
        )


@dataclass(frozen=True)
class ExitPlan:
    """Where to get out, and why there."""

    stop: float
    target: float
    stop_reason: str
    target_reason: str

    def reward_risk(self, entry: float) -> float:
        """Dollars of target per dollar of stop. Below 1 means the trade needs
        to be right more often than not just to break even."""
        risk = entry - self.stop
        if risk <= 0:
            return 0.0
        return (self.target - entry) / risk


def plan_exits(c: Chart, i: int, entry: float) -> ExitPlan:
    """Decide the stop and target for a trade opened at `entry`.

    Prefers real levels over arithmetic. A stop 2× the daily range below the
    entry is a number with no opinion about the chart; a stop just under the
    shelf that price bounced off is a statement — "if buyers there give up, I
    was wrong". Same for the target: aiming through a wall of sellers is how a
    winning trade turns into a round trip.

    Falls back to the ATR multiples whenever the chart has nothing to say,
    which is often, and that is fine.
    """
    atr = c.atr[i] or 0.0
    stop = entry - c.rules.stop_atr * atr
    target = entry + c.rules.target_atr * atr
    stop_reason = f"{c.rules.stop_atr:g}× the stock's normal daily range below entry"
    target_reason = f"{c.rules.target_atr:g}× the normal daily range above entry"

    if not c.rules.use_levels_for_exits or atr <= 0:
        return ExitPlan(stop, target, stop_reason, target_reason)

    land = c.landscape
    buffer = 0.25 * atr  # a little room, so ordinary noise does not trip it

    # Stop: under the shelf we are standing on, else under the nearest floor.
    zone = land.standing_in(i, "demand", c.rules.zone_max_tests) or land.demand_below(
        i, entry, c.rules.zone_max_tests
    )
    if zone is not None and zone.bottom < entry:
        candidate = zone.bottom - buffer
        # Only if it is not absurdly far -- a huge shelf would mean a huge loss.
        if entry - candidate <= 3.0 * c.rules.stop_atr * atr:
            stop, stop_reason = (
                candidate,
                f"just under the demand shelf at ${zone.bottom:,.2f}",
            )
    else:
        support = land.support_below(i, entry)
        if support is not None:
            candidate = support.price - buffer
            if entry - candidate <= 3.0 * c.rules.stop_atr * atr:
                stop, stop_reason = (
                    candidate,
                    f"just under support at ${support.price:,.2f}, which has held "
                    f"{support.strength} times",
                )

    # Target: stop short of the next ceiling rather than aiming through it.
    supply = land.supply_above(i, entry)
    resistance = land.resistance_above(i, entry)
    ceiling, ceiling_why = None, ""
    if supply is not None:
        ceiling, ceiling_why = supply.bottom, f"the supply shelf at ${supply.bottom:,.2f}"
    if resistance is not None and (ceiling is None or resistance.price < ceiling):
        ceiling = resistance.price
        ceiling_why = (
            f"resistance at ${resistance.price:,.2f}, which has capped price "
            f"{resistance.strength} times"
        )
    if ceiling is not None:
        candidate = ceiling - buffer
        if candidate > entry:
            target, target_reason = candidate, f"just below {ceiling_why}"

    return ExitPlan(stop, target, stop_reason, target_reason)


def ema_exit(c: Chart, i: int) -> str | None:
    """Optional tight trailing exit: a close below the fast line.

    The 8-day exponential average sits right on top of a trending stock, so
    losing it is the earliest honest sign the move has stalled. It gets you out
    near the highs when it works — and cuts plenty of trades that would have
    kept going when it does not. Off by default for that reason.
    """
    if not c.rules.use_ema_exit:
        return None
    line = c.ema_fast[i]
    if line is None or c.close[i] >= line:
        return None
    return (
        f"Closed below its {c.rules.fast_ema_days}-day line at ${line:,.2f} — "
        f"the move has stalled"
    )


def breakdown_exit(c: Chart, i: int, entry_price: float) -> str | None:
    """Shared across every strategy: a floor gave way, so get out.

    This bot is long-only, so the useful reading of a downside break is "leave",
    not "bet against it". Shorting needs borrow handling and has no ceiling on
    the loss — not a first bot's problem to solve.
    """
    if not c.rules.use_breakdown_exit:
        return None
    level = c.landscape.broke_below(i, entry_price)
    if level is None:
        return None
    return (
        f"Broke below support at ${level.price:,.2f}, a floor that had held "
        f"{level.strength} times — getting out"
    )


STRATEGIES: dict[str, Strategy] = {
    s.key: s for s in (BuyTheDip(), Breakout(), TrendChange(), ZoneBounce())
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
    "use_ema_exit": {
        "label": "Sell when it closes below the 8-day line",
        "help": "A tight trailing exit that gets you out as soon as momentum stalls. Locks in gains earlier, but also cuts winners short more often.",
        "type": "bool",
    },
    "fast_ema_days": {
        "label": "Fast line",
        "help": "The short exponential average drawn on every chart. 8 days is the usual swing-trading choice.",
        "type": "int", "min": 3, "max": 50, "unit": "days",
    },
    "pivot_reach": {
        "label": "Turning point size",
        "help": "How many days either side a high must beat to count as a turning point. Bigger finds fewer, more meaningful turns.",
        "type": "int", "min": 1, "max": 15, "unit": "days",
    },
    "level_tolerance_percent": {
        "label": "Same-level tolerance",
        "help": "Turning points within this percent of each other are treated as the same level.",
        "type": "float", "min": 0.1, "max": 5, "step": 0.1, "unit": "%",
    },
    "min_touches": {
        "label": "Touches before it counts",
        "help": "How many times price must have turned at a price before it is treated as a real level.",
        "type": "int", "min": 2, "max": 6,
    },
    "zone_impulse_atr": {
        "label": "Shelf strength",
        "help": "How violently price must leave a quiet patch for it to count as a shelf, in multiples of a normal day's range.",
        "type": "float", "min": 0.5, "max": 6, "step": 0.1, "unit": "× daily range",
    },
    "zone_base_bars": {
        "label": "Shelf width",
        "help": "How many quiet days before the move make up the shelf.",
        "type": "int", "min": 1, "max": 8, "unit": "days",
    },
    "zone_max_tests": {
        "label": "Allowed revisits",
        "help": "A shelf is eaten away each time price returns. 0 means only shelves price has never been back to.",
        "type": "int", "min": 0, "max": 5,
    },
    "use_levels_for_exits": {
        "label": "Put stops and targets at real levels",
        "help": "Stop under the shelf, target below the next ceiling — instead of a fixed distance that ignores the chart.",
        "type": "bool",
    },
    "use_breakdown_exit": {
        "label": "Sell when support breaks",
        "help": "Closes the trade when a floor that had been holding gives way, rather than waiting for the stop.",
        "type": "bool",
    },
    "min_reward_risk": {
        "label": "Least acceptable reward for the risk",
        "help": "Skip a trade unless the target is at least this many times the distance to the stop. Below 1 you must be right most of the time just to break even.",
        "type": "float", "min": 0.5, "max": 6, "step": 0.1, "unit": "×",
    },
    "max_hold_days": {
        "label": "Give up after",
        "help": "Sell a trade that has gone nowhere, so your money isn't stuck in a dud.",
        "type": "int", "min": 1, "max": 250, "unit": "days",
    },
}


# Settings every strategy honours, whatever its entry rule. Kept separate so a
# new strategy inherits them without having to remember to list them.
SHARED_SETTINGS = [
    "fast_ema_days",
    "use_ema_exit",
    "use_levels_for_exits",
    "use_breakdown_exit",
    "min_reward_risk",
    "pivot_reach",
    "min_touches",
]


def describe_strategies() -> list[dict]:
    """Everything the app needs to render the strategy picker."""
    out = []
    for s in STRATEGIES.values():
        keys = list(s.uses) + [k for k in SHARED_SETTINGS if k not in s.uses]
        out.append(
            {
                "key": s.key,
                "name": s.name,
                "tagline": s.tagline,
                "best_for": s.best_for,
                "how_it_works": s.how_it_works,
                "settings": [
                    {"key": k, **SETTING_HELP[k]} for k in keys if k in SETTING_HELP
                ],
            }
        )
    return out
