"""The midday scanner: sift a wide list of stocks down to a few worth a look.

Your watchlist answers "should I buy the things I already chose?". The scanner
answers the harder question: "out of everything trading today, which handful
are actually set up for a swing trade right now?"

### Why midday

A swing setup is judged on daily bars, and a daily bar is only finished at the
close. So the scanner works on *yesterday's* completed day and uses today's
part-formed bar only as a sanity check on price and volume. Run around noon,
that gives you a few hours to look at what it found before the close, without
pretending the day is over.

### What it scores

Nothing here is a prediction. Each candidate is scored on how well it matches
the things a swing trade needs, and every point it scores comes with a sentence
explaining itself:

- the trend is up on the 200-day average, and the fast 8-day line agrees
- price is near a demand shelf or a support level, not floating in mid-air
- there is a real ceiling above, so the target is worth the risk
- it moves enough to be worth trading, but is not a lottery ticket
- it trades enough volume that an order will not move the price

A high score is an invitation to look at the chart. It is not a signal, and the
app never buys from a scan on its own.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

from .market import Market, MarketError
from .models import Money, Rules
from .strategies import build_chart, get_strategy, plan_exits

log = logging.getLogger(__name__)

# Below this a stock is too thin: an order moves the price against you and the
# daily bars are full of gaps that no rule handles well.
MIN_DOLLAR_VOLUME = 5_000_000
MIN_PRICE = 5.0
# A stock that moves 15% a day is not a swing trade, it is a coin flip.
MAX_DAILY_RANGE_PERCENT = 12.0
MIN_DAILY_RANGE_PERCENT = 0.8


@dataclass
class Idea:
    symbol: str
    score: int
    price: float
    as_of: str
    headline: str
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    stop_reason: str = ""
    target_reason: str = ""
    reward_risk: float = 0.0
    shares: int = 0
    cost: float = 0.0
    risking: float = 0.0
    strategy_agrees: bool = False
    strategy_says: str = ""
    source: str = ""

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "score": self.score,
            "price": round(self.price, 2),
            "as_of": self.as_of,
            "headline": self.headline,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "entry": round(self.entry, 2),
            "stop": round(self.stop, 2),
            "target": round(self.target, 2),
            "stop_reason": self.stop_reason,
            "target_reason": self.target_reason,
            "reward_risk": round(self.reward_risk, 2),
            "shares": self.shares,
            "cost": round(self.cost, 2),
            "risking": round(self.risking, 2),
            "strategy_agrees": self.strategy_agrees,
            "strategy_says": self.strategy_says,
            "source": self.source,
        }


async def build_universe(market: Market, watchlist: list[str], limit: int = 60) -> dict[str, str]:
    """The list of stocks to look at, each tagged with why it is in the list.

    Your watchlist always makes the cut. The rest comes from Alpaca's screener:
    the busiest names and the day's biggest movers, which is where setups
    actually appear.
    """
    universe: dict[str, str] = {s.upper(): "your watchlist" for s in watchlist}
    try:
        for row in await market.market_universe():
            symbol = row["symbol"]
            if symbol not in universe and len(universe) < limit:
                universe[symbol] = row["source"]
    except MarketError as exc:
        log.warning("Screener unavailable, scanning the watchlist only: %s", exc)
    return universe


async def scan(
    market: Market,
    watchlist: list[str],
    rules: Rules,
    money: Money,
    account: dict,
    *,
    limit: int = 60,
    keep: int = 12,
) -> dict:
    """Score every stock in the universe and return the best few."""
    universe = await build_universe(market, watchlist, limit)
    symbols = list(universe)
    if not symbols:
        return {"at": datetime.now().isoformat(timespec="seconds"), "ideas": [],
                "looked_at": 0, "rejected": []}

    # Enough history for the 200-day average to be defined from the start.
    need_days = max(rules.trend_days, 260) + 60

    history: dict[str, list] = {}
    # Batched: one request per chunk rather than one per symbol.
    for chunk in _chunks(symbols, 20):
        try:
            history.update(await market.history_for_signals(chunk, days=need_days))
        except MarketError as exc:
            log.warning("Could not load prices for %s: %s", chunk, exc)

    ideas: list[Idea] = []
    rejected: list[dict] = []

    for symbol in symbols:
        bars = history.get(symbol) or []
        idea, why_not = await asyncio.to_thread(
            _score_one, symbol, bars, rules, money, account, universe[symbol]
        )
        if idea is not None:
            ideas.append(idea)
        elif why_not:
            rejected.append({"symbol": symbol, "reason": why_not})

    ideas.sort(key=lambda x: (x.score, x.reward_risk), reverse=True)
    return {
        "at": datetime.now().isoformat(timespec="seconds"),
        "looked_at": len(symbols),
        "ideas": [i.as_dict() for i in ideas[:keep]],
        "rejected": rejected[:40],
    }


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _score_one(
    symbol: str, bars: list, rules: Rules, money: Money, account: dict, source: str
) -> tuple[Idea | None, str]:
    """Grade one stock. Returns (idea, why_it_was_rejected)."""
    if len(bars) < max(rules.trend_days, 120):
        return None, "not enough price history"

    chart = build_chart(bars, rules)
    # Yesterday's finished day. Today's bar is still forming.
    i = len(bars) - 2 if len(bars) >= 2 else len(bars) - 1
    price = chart.close[i]
    atr = chart.atr[i]
    if atr is None or atr <= 0:
        return None, "could not measure its daily range"

    # -- the sanity gates ---------------------------------------------------
    if price < MIN_PRICE:
        return None, f"trades under ${MIN_PRICE:.0f}"

    dollar_volume = price * (chart.vol_avg[i] or chart.volume[i])
    if dollar_volume < MIN_DOLLAR_VOLUME:
        return None, "too thinly traded"

    range_percent = atr / price * 100
    if range_percent > MAX_DAILY_RANGE_PERCENT:
        return None, f"too wild — it swings {range_percent:.0f}% a day"
    if range_percent < MIN_DAILY_RANGE_PERCENT:
        return None, f"too sleepy — it barely moves ({range_percent:.1f}% a day)"

    score = 0
    reasons: list[str] = []
    warnings: list[str] = []

    # -- trend --------------------------------------------------------------
    trend = chart.trend[i]
    if trend is None:
        return None, f"needs {rules.trend_days} days of prices"
    if price < trend:
        return None, f"in a downtrend, {(trend - price) / trend * 100:.0f}% below its {rules.trend_days}-day average"

    score += 25
    reasons.append(
        f"In an uptrend — {(price / trend - 1) * 100:.0f}% above its "
        f"{rules.trend_days}-day average"
    )

    # The fast line says whether it is moving your way *now*, as opposed to
    # having been healthy for the last year.
    ema = chart.ema_fast[i]
    if ema is not None:
        if price > ema:
            score += 15
            reasons.append(
                f"Also above its {rules.fast_ema_days}-day line, so short-term "
                f"momentum agrees with the trend"
            )
        else:
            warnings.append(
                f"Below its {rules.fast_ema_days}-day line — still cooling off, "
                f"so it may have further to fall first"
            )

    # -- where it sits on the chart -----------------------------------------
    land = chart.landscape
    zone = land.standing_in(i, "demand", rules.zone_max_tests)
    near = zone or land.demand_below(i, price, rules.zone_max_tests)

    if zone is not None:
        score += 30
        reasons.append(
            f"Sitting right on a demand shelf (${zone.bottom:,.2f}–"
            f"${zone.top:,.2f}) that price left {zone.impulse_atr:.1f}× faster "
            f"than a normal day"
        )
    elif near is not None:
        away = (price - near.top) / price * 100
        if away <= 4:
            score += 18
            reasons.append(
                f"Close to a demand shelf at ${near.bottom:,.2f}–${near.top:,.2f}, "
                f"{away:.1f}% below"
            )
        else:
            warnings.append(
                f"Nearest demand shelf is {away:.0f}% below — a long way to fall first"
            )
    else:
        support = land.support_below(i, price)
        if support is not None and (price - support.price) / price * 100 <= 5:
            score += 12
            reasons.append(
                f"Just above support at ${support.price:,.2f}, which has held "
                f"{support.strength} times"
            )
        else:
            warnings.append("Nothing obvious holding it up nearby")

    # -- is there room above? ------------------------------------------------
    plan = plan_exits(chart, i, price)
    rr = plan.reward_risk(price)
    if rr >= rules.min_reward_risk * 1.5:
        score += 20
        reasons.append(f"Plenty of room to the next ceiling — {rr:.1f}× the risk")
    elif rr >= rules.min_reward_risk:
        score += 10
        reasons.append(f"Worth the risk — target is {rr:.1f}× the stop distance")
    else:
        warnings.append(
            f"Target is only {rr:.1f}× the risk, below your "
            f"{rules.min_reward_risk:g}× minimum"
        )

    # -- does the chosen strategy agree? --------------------------------------
    verdict = get_strategy(rules.strategy).entry(chart, i)
    if verdict.buy:
        score += 25
        reasons.append(f"Your strategy says buy: {verdict.reason}")

    # -- sizing --------------------------------------------------------------
    per_share_risk = price - plan.stop
    shares = 0
    if per_share_risk > 0:
        budget = account.get("value", 0) * money.risk_percent / 100.0
        shares = int(budget // per_share_risk)
        shares = min(
            shares,
            int((account.get("value", 0) * money.max_position_percent / 100.0) // price),
            int(account.get("buying_power", 0) // price),
        )

    if score < 40:
        return None, "did not score highly enough"

    # Lead with what makes this one different. Every candidate is in an uptrend
    # by definition -- if that were the headline, every card would read the same
    # and the list would be useless to skim.
    headline = next(
        (r for r in reasons if "shelf" in r or "support" in r),
        next(
            (r for r in reasons if r.startswith("Your strategy")),
            next((r for r in reasons if "room" in r or "Worth the risk" in r),
                 reasons[0] if reasons else ""),
        ),
    )

    return (
        Idea(
            symbol=symbol,
            score=min(score, 100),
            price=price,
            as_of=chart.bars[i].day.isoformat(),
            headline=headline,
            reasons=reasons,
            warnings=warnings,
            entry=price,
            stop=plan.stop,
            target=plan.target,
            stop_reason=plan.stop_reason,
            target_reason=plan.target_reason,
            reward_risk=rr,
            shares=max(shares, 0),
            cost=max(shares, 0) * price,
            risking=max(shares, 0) * per_share_risk,
            strategy_agrees=verdict.buy,
            strategy_says=verdict.reason,
            source=source,
        ),
        "",
    )
