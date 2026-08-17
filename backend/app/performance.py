"""How the live paper account is actually doing.

The backtest grades a strategy against the past. This grades it against
*reality* — the same questions, asked of trades that really happened.

Two sources, deliberately:

- **The equity curve comes from Alpaca**, not from anything this app recorded.
  Alpaca counts every fill, fee and position mark whether or not this app was
  running, so it cannot be flattered by a missed update or a restart.
- **The trade list is rebuilt from fills**, matched oldest-buy-first per stock.
  A "trade" is a completed round trip: shares bought, then sold. Positions
  still open are reported separately, because counting a paper gain as a
  result is how people convince themselves a losing system works.

The same buy-and-hold comparison as the backtest is included, over exactly the
period the account has been running. If a strategy trades for a month and ends
up behind SPY, that is the headline, not a footnote.
"""

from collections import defaultdict, deque
from datetime import date, datetime

from .indicators import pct_change
from .models import Bar

BENCHMARK = "SPY"

# Orders this app placed carry one of these prefixes in their client order id.
# Anything else on the account -- another bot, a manual trade in Alpaca's own
# dashboard -- belongs to someone else and must not be scored as ours.
OUR_TAGS = ("swing-", "hand-")


def ours(fill: dict) -> bool:
    return str(fill.get("tag") or "").startswith(OUR_TAGS)


def round_trips(fills: list[dict]) -> tuple[list[dict], dict[str, list]]:
    """Match sells against earlier buys, oldest lot first.

    Returns the completed trades and whatever buy lots are still open. Partial
    fills are handled by splitting a lot, so selling half a position produces
    one closed trade and leaves the rest open.
    """
    lots: dict[str, deque] = defaultdict(deque)
    trades: list[dict] = []

    for fill in fills:
        symbol = fill["symbol"]
        if fill["side"] == "buy":
            lots[symbol].append(
                {"shares": fill["shares"], "price": fill["price"], "at": fill["at"]}
            )
            continue

        remaining = fill["shares"]
        while remaining > 1e-9 and lots[symbol]:
            lot = lots[symbol][0]
            take = min(remaining, lot["shares"])
            profit = (fill["price"] - lot["price"]) * take
            held = (fill["at"] - lot["at"]).days
            trades.append(
                {
                    "symbol": symbol,
                    "shares": round(take, 4),
                    "bought_at": lot["at"].date().isoformat(),
                    "bought_for": round(lot["price"], 2),
                    "sold_at": fill["at"].date().isoformat(),
                    "sold_for": round(fill["price"], 2),
                    "profit": round(profit, 2),
                    "profit_percent": round(pct_change(lot["price"], fill["price"]), 2),
                    "held_days": max(held, 0),
                    "win": profit > 0,
                }
            )
            lot["shares"] -= take
            remaining -= take
            if lot["shares"] <= 1e-9:
                lots[symbol].popleft()

        # A sell with no matching buy means the position predates this window.
        # Ignored rather than guessed at -- a made-up cost basis is worse than
        # a missing trade.

    open_lots = {s: list(q) for s, q in lots.items() if q}
    return trades, open_lots


def summarise(
    curve: list[dict],
    trades: list[dict],
    positions: list[dict],
    benchmark_bars: list[Bar],
) -> dict:
    """Turn the raw history into the numbers and the sentence."""
    started = curve[0]["value"] if curve else 0.0
    now = curve[-1]["value"] if curve else 0.0

    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    gross_win = sum(t["profit"] for t in wins)
    gross_loss = abs(sum(t["profit"] for t in losses))
    realised = gross_win - gross_loss
    unrealised = sum(p["profit"] for p in positions)

    peak = 0.0
    worst = 0.0
    for point in curve:
        peak = max(peak, point["value"])
        if peak > 0:
            worst = max(worst, (peak - point["value"]) / peak * 100)

    days = 0
    if len(curve) >= 2:
        days = (
            date.fromisoformat(curve[-1]["day"]) - date.fromisoformat(curve[0]["day"])
        ).days

    bench = None
    if benchmark_bars and curve:
        first = benchmark_bars[0].close
        last = benchmark_bars[-1].close
        bench = {
            "symbol": BENCHMARK,
            "return_percent": round(pct_change(first, last), 2),
        }

    total_return = pct_change(started, now) if started else 0.0

    stats = {
        "started_with": round(started, 2),
        "value_now": round(now, 2),
        "change": round(now - started, 2),
        "return_percent": round(total_return, 2),
        "days_running": days,
        "realised": round(realised, 2),
        "unrealised": round(unrealised, 2),
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "profit_factor": (
            round(gross_win / gross_loss, 2) if gross_loss > 0 else None
        ),
        "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss": round(gross_loss / len(losses), 2) if losses else 0.0,
        "avg_hold_days": (
            round(sum(t["held_days"] for t in trades) / len(trades), 1) if trades else 0
        ),
        "worst_drop_percent": round(worst, 2),
        "open_positions": len(positions),
        "benchmark": bench,
        "beat_benchmark_by": (
            round(total_return - bench["return_percent"], 2) if bench else None
        ),
    }
    stats["plain_english"] = _sentence(stats)
    return stats


def _sentence(s: dict) -> str:
    if s["days_running"] < 1:
        return "The account has not been running long enough to say anything yet."

    if s["trades"] == 0:
        base = (
            f"In {s['days_running']} days the bot has not completed a single "
            f"round trip yet"
        )
        if s["open_positions"]:
            base += (
                f", though it is holding {s['open_positions']} position"
                f"{'s' if s['open_positions'] != 1 else ''} right now"
            )
        return base + ". Judge nothing from this."

    direction = "up" if s["change"] >= 0 else "down"
    text = (
        f"Over {s['days_running']} days the account is {direction} "
        f"${abs(s['change']):,.0f} ({s['return_percent']:+.1f}%), from "
        f"{s['trades']} completed trade{'s' if s['trades'] != 1 else ''} — "
        f"{s['wins']} won, {s['losses']} lost, held about "
        f"{s['avg_hold_days']:.0f} days each."
    )
    if s["benchmark"]:
        text += (
            f" Over the same stretch {s['benchmark']['symbol']} returned "
            f"{s['benchmark']['return_percent']:+.1f}%."
        )
    if s["trades"] < 20:
        text += (
            " That is far too few trades to mean anything — at this sample size "
            "a good result and a lucky one look identical."
        )
    return text


def is_too_early(stats: dict) -> bool:
    """Whether to warn that these numbers cannot support a conclusion."""
    return stats["trades"] < 20 or stats["days_running"] < 30
