"""Shared plain data types. No logic, no I/O -- so everything can import these."""

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class Bar:
    """One trading day for one stock."""

    symbol: str
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Rules:
    """Every setting a person can change, with beginner-facing defaults.

    One flat object for all strategies. Each strategy reads only the fields it
    needs, which keeps the settings screen a single form instead of three.
    """

    strategy: str = "buy_the_dip"

    # --- The safety filter every strategy shares ---------------------------
    # "Only buy a stock that is in a long-term uptrend."
    use_trend_filter: bool = True
    trend_days: int = 200
    # The fast line. An 8-day exponential average hugs price closely, so it is
    # the usual reference for "is this still moving my way?" -- drawn on every
    # chart next to the 200-day, and optionally used as a trailing exit.
    fast_ema_days: int = 8
    use_ema_exit: bool = False

    # --- Support, resistance, and supply/demand zones -----------------------
    # How many days either side a bar must beat to count as a turning point.
    # Bigger finds fewer, more meaningful turns -- and confirms them later.
    pivot_reach: int = 3
    # Turning points within this percent of each other are the same level.
    level_tolerance_percent: float = 0.6
    # How many touches before a price counts as a level at all.
    min_touches: int = 2
    # How violently price must leave a base for it to be a zone, in daily ranges.
    zone_impulse_atr: float = 1.6
    zone_base_bars: int = 3
    # A zone revisited more than this many times is considered used up.
    zone_max_tests: int = 1
    # Put the stop under a demand zone and the target beneath the next supply
    # zone, instead of at a fixed distance that ignores the chart.
    use_levels_for_exits: bool = True
    # Sell when a support level that had been holding gives way.
    use_breakdown_exit: bool = True
    # Refuse a trade whose target is not worth the risk of its stop.
    min_reward_risk: float = 1.5

    # --- How the trade is exited ------------------------------------------
    # Stop and target are measured in ATR: the stock's own typical daily
    # range. A volatile stock gets a wider stop than a sleepy one, which is
    # the whole point -- a fixed 5% stop is far too tight for one and far too
    # loose for the other.
    atr_days: int = 14
    stop_atr: float = 2.0
    target_atr: float = 4.0
    trail_atr: float = 0.0  # 0 = no trailing stop
    max_hold_days: int = 20

    # --- "Buy the dip" ------------------------------------------------------
    rsi_days: int = 14
    rsi_buy_below: float = 40.0

    # --- "Breakout" ---------------------------------------------------------
    breakout_days: int = 20
    volume_multiple: float = 1.5

    # --- "Trend change" -----------------------------------------------------
    fast_days: int = 20
    slow_days: int = 50


@dataclass(frozen=True)
class Money:
    """How much to put at risk, and what trading costs to assume."""

    starting_cash: float = 10_000.0
    # Percent of the account risked between entry and stop on each trade.
    risk_percent: float = 1.0
    # Never let one position exceed this share of the account.
    max_position_percent: float = 25.0
    max_open_positions: int = 3
    # Alpaca charges no commission on US stocks; IBKR and others do.
    commission_per_trade: float = 0.0
    commission_per_share: float = 0.0
    # The gap between the price you saw and the price you got, in basis
    # points (5 bps = 0.05%). Applied on the way in and the way out.
    slippage_bps: float = 5.0


def buy_price(price: float, m: Money) -> float:
    """What you actually pay: the price on screen plus slippage."""
    return price * (1 + m.slippage_bps / 10_000)


def sell_price(price: float, m: Money) -> float:
    """What you actually get: the price on screen minus slippage."""
    return price * (1 - m.slippage_bps / 10_000)


def commission(shares: int, m: Money) -> float:
    if not m.commission_per_trade and not m.commission_per_share:
        return 0.0
    return max(m.commission_per_trade, m.commission_per_share * shares)


@dataclass
class SimTrade:
    symbol: str
    entry_day: date
    entry_price: float
    shares: int
    stop: float
    target: float
    reason: str
    exit_day: date | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    # Why the stop and target sit where they do -- "under the shelf at $412"
    # rather than a bare number.
    stop_reason: str = ""
    target_reason: str = ""
    costs: float = 0.0
    # Highest close seen while the trade was open, for the trailing stop.
    peak: float = 0.0

    @property
    def held_days(self) -> int:
        if self.exit_day is None:
            return 0
        return (self.exit_day - self.entry_day).days

    @property
    def profit(self) -> float:
        """Dollars made or lost after costs."""
        if self.exit_price is None:
            return 0.0
        return (self.exit_price - self.entry_price) * self.shares - self.costs

    @property
    def profit_percent(self) -> float:
        if self.exit_price is None or not self.entry_price:
            return 0.0
        return (self.exit_price - self.entry_price) / self.entry_price * 100.0

    @property
    def is_win(self) -> bool:
        return self.profit > 0


@dataclass
class Verdict:
    """A strategy's answer for one day, with the reason spelled out."""

    buy: bool
    reason: str
    detail: dict = field(default_factory=dict)


@dataclass
class OpenSignal:
    """A buy the bot wants to make tomorrow morning."""

    symbol: str
    day: datetime
    reason: str
    price: float
    stop: float
    target: float
    shares: int
