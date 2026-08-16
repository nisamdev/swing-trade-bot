"""Where price has turned before, and the shelves it left behind.

Three ideas, built on top of each other:

**Pivots.** A day whose high is the highest of the few days either side is a
swing high; the mirror is a swing low. These are the raw turning points, and
everything else here is derived from them.

**Levels.** Turning points that happened at roughly the same price are the same
level. A price that stopped a move three times matters more than one that
stopped it once, so levels carry a touch count.

**Zones.** A level is a line, but price does not turn on a line — it turns in a
band. A zone is the quiet patch of trading right before price shot away from
it: the shelf where enough buyers (or sellers) were waiting to move the market.
A zone that price has not returned to yet is *fresh*, and fresh zones are the
ones that tend to hold.

### The rule that makes this honest

A pivot is not knowable on the day it happens. You only learn that Tuesday was
a swing low once Wednesday, Thursday and Friday have all failed to go lower. So
every level and zone here carries a ``confirmed_at`` index, and nothing may be
used before it. Skipping that is the single easiest way to build a backtest
that makes money on paper and nothing anywhere else — it is exactly the kind of
lookahead that makes support and resistance look magical in hindsight.
"""

from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date

from .indicators import Series
from .models import Bar

# Never keep more than this many of each. Old levels stop mattering, and an
# unbounded list makes every lookup slower for no benefit.
MAX_KEEP = 60


@dataclass(frozen=True)
class Pivot:
    index: int
    day: date
    price: float
    kind: str  # "high" or "low"
    confirmed_at: int


@dataclass
class Level:
    """A price that has stopped a move more than once."""

    price: float
    kind: str  # "resistance", "support", or "both"
    touches: list[Pivot] = field(default_factory=list)
    # Index from which this level may be used, i.e. when its second touch was
    # confirmed. Before this it is not yet a level, just one turning point.
    confirmed_at: int = 0

    @property
    def strength(self) -> int:
        return len(self.touches)

    @property
    def last_touch(self) -> int:
        return max(p.index for p in self.touches)

    def touches_by(self, i: int) -> int:
        """How many touches were confirmed on or before day `i`."""
        return sum(1 for p in self.touches if p.confirmed_at <= i)

    def active_at(self, i: int, min_touches: int) -> bool:
        return i >= self.confirmed_at and self.touches_by(i) >= min_touches

    def as_dict(self) -> dict:
        return {
            "price": round(self.price, 2),
            "kind": self.kind,
            "touches": self.strength,
            "first_day": min(p.day for p in self.touches).isoformat(),
            "last_day": max(p.day for p in self.touches).isoformat(),
        }


@dataclass
class Zone:
    """A band of price where one side clearly overwhelmed the other."""

    kind: str  # "demand" (buyers) or "supply" (sellers)
    bottom: float
    top: float
    base_start: int
    base_end: int
    confirmed_at: int
    day: date
    impulse_atr: float  # how violently price left, in daily ranges
    # Days on which price traded back into this band, oldest first.
    tests: list[int] = field(default_factory=list)

    @property
    def middle(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def height(self) -> float:
        return self.top - self.bottom

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    def touched_by_bar(self, bar: Bar) -> bool:
        return bar.low <= self.top and bar.high >= self.bottom

    def tests_before(self, i: int) -> int:
        """Visits strictly before day `i` — today's own visit does not count
        against the zone, or a zone could never be traded at all."""
        return bisect_left(self.tests, i)

    def fresh_at(self, i: int, max_tests: int) -> bool:
        return i >= self.confirmed_at and self.tests_before(i) <= max_tests

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "bottom": round(self.bottom, 2),
            "top": round(self.top, 2),
            "day": self.day.isoformat(),
            "tests": len(self.tests),
            "strength": round(self.impulse_atr, 1),
        }


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #


def find_pivots(bars: list[Bar], reach: int = 3) -> list[Pivot]:
    """Turning points: a bar that is the extreme of the `reach` bars each side.

    Larger `reach` finds fewer, more significant turns. It also delays
    confirmation by that many days, which is the honest cost of certainty.
    """
    if reach < 1:
        raise ValueError("reach must be >= 1")

    out: list[Pivot] = []
    n = len(bars)
    for i in range(reach, n - reach):
        window = bars[i - reach : i + reach + 1]
        high, low = bars[i].high, bars[i].low

        if high >= max(b.high for b in window):
            # A flat top produces a pivot on each bar; keep only the first.
            if not (out and out[-1].kind == "high" and out[-1].index >= i - reach):
                out.append(Pivot(i, bars[i].day, high, "high", i + reach))
        if low <= min(b.low for b in window):
            if not (out and out[-1].kind == "low" and out[-1].index >= i - reach):
                out.append(Pivot(i, bars[i].day, low, "low", i + reach))
    return out


def build_levels(
    pivots: list[Pivot], tolerance_percent: float = 0.6, min_touches: int = 2
) -> list[Level]:
    """Group turning points that happened at nearly the same price."""
    if not pivots:
        return []

    groups: list[list[Pivot]] = []
    for pivot in sorted(pivots, key=lambda p: p.price):
        if groups:
            mean = sum(p.price for p in groups[-1]) / len(groups[-1])
            if mean and abs(pivot.price - mean) / mean * 100 <= tolerance_percent:
                groups[-1].append(pivot)
                continue
        groups.append([pivot])

    levels: list[Level] = []
    for group in groups:
        if len(group) < min_touches:
            continue
        kinds = {p.kind for p in group}
        # A level that has acted as both a ceiling and a floor is the strongest
        # kind there is -- price remembers it either way.
        kind = (
            "both" if len(kinds) > 1
            else "resistance" if "high" in kinds
            else "support"
        )
        # Usable only once the min_touches-th touch has been confirmed.
        confirmed = sorted(p.confirmed_at for p in group)[min_touches - 1]
        levels.append(
            Level(
                price=sum(p.price for p in group) / len(group),
                kind=kind,
                touches=sorted(group, key=lambda p: p.index),
                confirmed_at=confirmed,
            )
        )

    levels.sort(key=lambda lv: lv.last_touch, reverse=True)
    return levels[:MAX_KEEP]


def build_zones(
    bars: list[Bar],
    atr: Series,
    impulse_atr: float = 1.6,
    base_bars: int = 3,
    leg_bars: int = 3,
    base_atr: float = 1.5,
) -> list[Zone]:
    """Find the quiet bases that price exploded away from.

    Walks forward looking for an impulsive run, then marks the calm bars
    immediately before it as the zone. Zones that mostly overlap an existing
    one are dropped, so a long rally does not leave twenty copies of itself.
    """
    zones: list[Zone] = []
    n = len(bars)

    for start in range(base_bars, n - leg_bars):
        a = atr[start]
        if not a or a <= 0:
            continue

        end = start + leg_bars - 1
        move = bars[end].close - bars[start].open
        strength = abs(move) / a
        if strength < impulse_atr:
            continue

        base = bars[start - base_bars : start]
        if not base:
            continue
        bottom = min(b.low for b in base)
        top = max(b.high for b in base)
        # The base has to actually be a base. A wild swing before an impulse is
        # not a shelf, it is just more noise.
        if (top - bottom) > base_atr * a:
            continue

        zone = Zone(
            kind="demand" if move > 0 else "supply",
            bottom=bottom,
            top=top,
            base_start=start - base_bars,
            base_end=start - 1,
            confirmed_at=end,
            day=bars[start - 1].day,
            impulse_atr=strength,
        )

        if any(_overlaps(zone, other) for other in zones if other.kind == zone.kind):
            continue
        zones.append(zone)

    # Record every later visit, so freshness can be asked about at any day.
    for zone in zones:
        for i in range(zone.confirmed_at + 1, n):
            if zone.touched_by_bar(bars[i]):
                zone.tests.append(i)

    zones.sort(key=lambda z: z.confirmed_at, reverse=True)
    return zones[:MAX_KEEP]


def _overlaps(a: Zone, b: Zone, share: float = 0.6) -> bool:
    span = min(a.top, b.top) - max(a.bottom, b.bottom)
    if span <= 0:
        return False
    smaller = min(a.height, b.height) or 1e-9
    return span / smaller >= share


# --------------------------------------------------------------------------- #
# Asking questions about them
# --------------------------------------------------------------------------- #


class Landscape:
    """Everything known about one stock's levels and zones, queried by day.

    Every method takes the day index `i` and answers as of that day only.
    """

    def __init__(
        self,
        bars: list[Bar],
        atr: Series,
        *,
        pivot_reach: int = 3,
        level_tolerance_percent: float = 0.6,
        min_touches: int = 2,
        zone_impulse_atr: float = 1.6,
        zone_base_bars: int = 3,
    ) -> None:
        self.bars = bars
        self.min_touches = min_touches
        self.pivots = find_pivots(bars, pivot_reach)
        self.levels = build_levels(self.pivots, level_tolerance_percent, min_touches)
        self.zones = build_zones(
            bars, atr, impulse_atr=zone_impulse_atr, base_bars=zone_base_bars
        )

    # -- levels ---------------------------------------------------------- #

    def resistance_above(self, i: int, price: float) -> Level | None:
        """The nearest ceiling above today's price."""
        candidates = [
            lv for lv in self.levels
            if lv.active_at(i, self.min_touches) and lv.price > price * 1.001
        ]
        return min(candidates, key=lambda lv: lv.price) if candidates else None

    def support_below(self, i: int, price: float) -> Level | None:
        """The nearest floor below today's price."""
        candidates = [
            lv for lv in self.levels
            if lv.active_at(i, self.min_touches) and lv.price < price * 0.999
        ]
        return max(candidates, key=lambda lv: lv.price) if candidates else None

    def broke_below(self, i: int, reference: float) -> Level | None:
        """A floor that was under us and has now given way.

        Used as a sell signal: this bot never shorts, so the useful reading of
        a downside break is "get out", not "bet against it".
        """
        if i < 1:
            return None
        today = self.bars[i].close
        broken = [
            lv for lv in self.levels
            if lv.active_at(i, self.min_touches)
            and lv.price < reference
            and self.bars[i - 1].close >= lv.price > today
        ]
        return max(broken, key=lambda lv: lv.price) if broken else None

    # -- zones ------------------------------------------------------------ #

    def demand_below(self, i: int, price: float, max_tests: int = 1) -> Zone | None:
        """The nearest fresh demand shelf beneath today's price."""
        candidates = [
            z for z in self.zones
            if z.kind == "demand" and z.fresh_at(i, max_tests) and z.top < price
        ]
        return max(candidates, key=lambda z: z.top) if candidates else None

    def supply_above(self, i: int, price: float, max_tests: int = 99) -> Zone | None:
        """The nearest ceiling of sellers above today's price.

        Freshness is not required by default: an old supply zone still marks
        where a target should stop short, even if it has been tested since.
        """
        candidates = [
            z for z in self.zones
            if z.kind == "supply" and z.fresh_at(i, max_tests) and z.bottom > price
        ]
        return min(candidates, key=lambda z: z.bottom) if candidates else None

    def standing_in(self, i: int, kind: str, max_tests: int = 1) -> Zone | None:
        """A zone today's bar has traded into."""
        bar = self.bars[i]
        candidates = [
            z for z in self.zones
            if z.kind == kind and z.fresh_at(i, max_tests) and z.touched_by_bar(bar)
        ]
        return max(candidates, key=lambda z: z.confirmed_at) if candidates else None

    # -- for the chart ----------------------------------------------------- #

    def visible(self, i: int, price: float, span: float = 0.35) -> dict:
        """Levels and zones near the current price, for drawing."""
        lo, hi = price * (1 - span), price * (1 + span)
        return {
            "levels": [
                lv.as_dict() for lv in self.levels
                if lv.active_at(i, self.min_touches) and lo <= lv.price <= hi
            ][:12],
            "zones": [
                z.as_dict() for z in self.zones
                if z.confirmed_at <= i and lo <= z.middle <= hi
            ][:12],
        }
