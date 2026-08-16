"""Replay a strategy over real historical prices and report what would have happened.

The point of this file is to be *pessimistic*. A backtest that flatters a
strategy is worse than no backtest at all, because it costs real money to find
out. So:

- A signal on Monday's close buys at Tuesday's *open*, never at Monday's close.
  You cannot trade a price that has already happened.
- Every fill pays slippage: you get a slightly worse price than the one on
  screen, in both directions.
- If a day's range contains both the stop and the target, the stop is assumed
  to have hit first. Daily bars cannot tell us the order, so we take the
  unkind reading.
- A gap through the stop fills at the open, not at the stop price -- which is
  exactly what a real stop order does to you.
- Money is finite. A signal with no cash left simply does not trade, and the
  result says so.

It also always reports buy-and-hold over the same period, because "did this
beat just owning the stock?" is the only question that decides whether the
strategy was worth the effort.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from .indicators import pct_change
from .models import (
    Bar,
    Money,
    Rules,
    SimTrade,
    buy_price as _buy_price,
    commission as _commission,
    sell_price as _sell_price,
)
from .strategies import (
    Chart,
    breakdown_exit,
    build_chart,
    ema_exit,
    get_strategy,
    plan_exits,
)

TRADING_DAYS_PER_YEAR = 252

# Below this many trades the numbers are noise, whatever they say.
MIN_TRADES_FOR_CONFIDENCE = 20


@dataclass
class Fill:
    """A queued order waiting for tomorrow's opening price."""

    symbol: str
    reason: str
    # Entries only. Worked out at the signal bar, so the stop and target
    # reflect what was knowable when the decision was made. Level-based exits
    # are absolute prices -- a shelf does not move because we bought.
    atr: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    stop_reason: str = ""
    target_reason: str = ""


@dataclass
class Sim:
    money: Money
    cash: float = 0.0
    positions: dict[str, SimTrade] = field(default_factory=dict)
    trades: list[SimTrade] = field(default_factory=list)
    curve: list[tuple[date, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    skipped_no_cash: int = 0

    def equity(self, prices: dict[str, float]) -> float:
        held = sum(
            t.shares * prices.get(sym, t.entry_price)
            for sym, t in self.positions.items()
        )
        return self.cash + held


def run_backtest(
    bars_by_symbol: dict[str, list[Bar]],
    rules: Rules,
    money: Money,
    trade_from: date | None = None,
) -> dict:
    """Simulate one shared pot of money traded across every symbol given.

    `bars_by_symbol` may start earlier than `trade_from`. Those extra bars are
    warm-up: the averages need history behind them, but no trade happens before
    `trade_from`, so the reported period is exactly the one that was asked for.
    """
    strategy = get_strategy(rules.strategy)

    charts: dict[str, Chart] = {}
    index: dict[str, dict[date, int]] = {}
    for symbol, bars in bars_by_symbol.items():
        if len(bars) < 2:
            continue
        charts[symbol] = build_chart(bars, rules)
        index[symbol] = {b.day: i for i, b in enumerate(bars)}

    if not charts:
        raise ValueError("No price history came back for those symbols and dates.")

    all_days = sorted(
        d
        for idx in index.values()
        for d in idx
        if trade_from is None or d >= trade_from
    )
    if len(all_days) < 2:
        raise ValueError(
            "That date range contains almost no trading days. Try a longer period."
        )
    all_days = sorted(set(all_days))
    sim = Sim(money=money, cash=money.starting_cash)

    pending_entries: list[Fill] = []
    pending_exits: list[Fill] = []
    last_close: dict[str, float] = {}

    for day in all_days:
        todays_bar: dict[str, Bar] = {}
        todays_i: dict[str, int] = {}
        for symbol, idx in index.items():
            i = idx.get(day)
            if i is not None:
                todays_i[symbol] = i
                todays_bar[symbol] = charts[symbol].bars[i]

        # --- 1. Yesterday's sell decisions fill at today's open -------------
        still_pending: list[Fill] = []
        for order in pending_exits:
            bar = todays_bar.get(order.symbol)
            trade = sim.positions.get(order.symbol)
            if trade is None:
                continue  # a stop or target already closed it
            if bar is None:
                still_pending.append(order)  # stock did not trade today
                continue
            _close_trade(sim, trade, day, _sell_price(bar.open, money), order.reason)
        pending_exits = still_pending

        # --- 2. Yesterday's buy decisions fill at today's open --------------
        still_pending = []
        for order in pending_entries:
            bar = todays_bar.get(order.symbol)
            if bar is None:
                still_pending.append(order)
                continue
            if order.symbol in sim.positions:
                continue
            if len(sim.positions) >= money.max_open_positions:
                sim.notes.append(
                    f"{day}: skipped {order.symbol} — already holding the maximum "
                    f"{money.max_open_positions} positions"
                )
                continue
            _open_trade(sim, order, day, bar, rules, last_close)
        pending_entries = still_pending

        # --- 3. Stops and targets, which fire during the day ----------------
        for symbol, trade in list(sim.positions.items()):
            bar = todays_bar.get(symbol)
            if bar is None or trade.entry_day > day:
                continue
            # The trail ratchets on yesterday's peak, before today's range is
            # examined -- a stop cannot be moved using a price it then hits.
            atr = charts[symbol].atr[todays_i[symbol]]
            apply_trailing_stop(trade, atr or 0.0, rules.trail_atr)
            _check_stop_and_target(sim, trade, bar, day, money)

        # --- 4. Close of day: decide what to do tomorrow --------------------
        queued_exit = {o.symbol for o in pending_exits}
        for symbol, trade in sim.positions.items():
            if symbol in queued_exit or symbol not in todays_i:
                continue
            c, i = charts[symbol], todays_i[symbol]
            reason = strategy.exit(c, i, trade)
            if reason is None:
                reason = breakdown_exit(c, i, trade.entry_price)
            if reason is None:
                reason = ema_exit(c, i)
            if reason is None and (day - trade.entry_day).days >= rules.max_hold_days:
                reason = (
                    f"Held {rules.max_hold_days} days without hitting the target — "
                    f"freeing the money up"
                )
            if reason:
                pending_exits.append(Fill(symbol=symbol, reason=reason))

        queued_entry = {o.symbol for o in pending_entries}
        room = money.max_open_positions - len(sim.positions) - len(pending_entries)
        if room > 0:
            for symbol, i in todays_i.items():
                if room <= 0:
                    break
                if symbol in sim.positions or symbol in queued_entry:
                    continue
                c = charts[symbol]
                atr = c.atr[i]
                if atr is None or atr <= 0:
                    continue
                verdict = strategy.entry(c, i)
                if not verdict.buy:
                    continue

                # Where would we get out? Decided now, from the chart as it
                # stands today, not from tomorrow's price.
                plan = plan_exits(c, i, c.close[i])
                rr = plan.reward_risk(c.close[i])
                if rr < rules.min_reward_risk:
                    sim.notes.append(
                        f"{day}: skipped {symbol} — the target is only {rr:.1f}× "
                        f"the risk, below the {rules.min_reward_risk:g}× minimum"
                    )
                    continue

                pending_entries.append(
                    Fill(
                        symbol=symbol,
                        reason=verdict.reason,
                        atr=atr,
                        stop=plan.stop,
                        target=plan.target,
                        stop_reason=plan.stop_reason,
                        target_reason=plan.target_reason,
                    )
                )
                room -= 1

        # --- 5. Mark the account to market ---------------------------------
        for symbol, bar in todays_bar.items():
            last_close[symbol] = bar.close
        sim.curve.append((day, sim.equity(last_close)))

    # Anything still open is valued at the final close, not left dangling.
    final_day = all_days[-1]
    for symbol, trade in list(sim.positions.items()):
        price = last_close.get(symbol, trade.entry_price)
        _close_trade(
            sim, trade, final_day, _sell_price(price, money),
            "Still open when the test period ended — valued at the last price",
        )

    return _report(sim, charts, rules, money, all_days)


# --------------------------------------------------------------------------- #
# Trade mechanics
# --------------------------------------------------------------------------- #


def _open_trade(
    sim: Sim, order: Fill, day: date, bar: Bar, rules: Rules, prices: dict[str, float]
) -> None:
    money = sim.money
    entry = _buy_price(bar.open, money)
    stop, target = order.stop, order.target

    # The plan was drawn on yesterday's close. If the stock gapped overnight
    # past either end of it, the setup no longer exists -- taking the trade
    # anyway would mean buying at a price the plan never contemplated.
    if not (stop < entry < target):
        sim.notes.append(
            f"{day}: skipped {order.symbol} — it gapped past the plan overnight "
            f"(opened ${bar.open:,.2f}, stop ${stop:,.2f}, target ${target:,.2f})"
        )
        return

    per_share_risk = entry - stop
    rr = (target - entry) / per_share_risk
    if rr < rules.min_reward_risk:
        sim.notes.append(
            f"{day}: skipped {order.symbol} — after the open the target was only "
            f"{rr:.1f}× the risk"
        )
        return

    equity = sim.equity(prices)
    risk_budget = equity * money.risk_percent / 100.0
    shares = int(risk_budget // per_share_risk)

    # Two ceilings: never let one name dominate, and never spend cash we
    # do not have. Both are why a "great" signal can still not trade.
    cap_by_size = int((equity * money.max_position_percent / 100.0) // entry)
    cap_by_cash = int(sim.cash // entry)
    shares = min(shares, cap_by_size, cap_by_cash)

    if shares < 1:
        sim.skipped_no_cash += 1
        sim.notes.append(
            f"{day}: skipped {order.symbol} — not enough spare cash for even one share"
        )
        return

    cost = _commission(shares, money)
    sim.cash -= shares * entry + cost
    sim.positions[order.symbol] = SimTrade(
        symbol=order.symbol,
        entry_day=day,
        entry_price=entry,
        shares=shares,
        stop=stop,
        target=target,
        reason=order.reason,
        stop_reason=order.stop_reason,
        target_reason=order.target_reason,
        costs=cost,
        peak=bar.close,
    )


def _check_stop_and_target(
    sim: Sim, trade: SimTrade, bar: Bar, day: date, money: Money
) -> None:
    # A gap straight through the stop fills at the open. This is the single
    # biggest source of losses larger than the ones you planned for.
    if bar.open <= trade.stop:
        _close_trade(
            sim, trade, day, _sell_price(bar.open, money),
            f"Gapped below the stop and sold at the open, ${bar.open:,.2f}",
        )
        return

    # Stop before target: if both sit inside one day's range, daily bars
    # cannot say which came first, so assume the unkind one.
    if bar.low <= trade.stop:
        _close_trade(
            sim, trade, day, _sell_price(trade.stop, money),
            f"Hit the stop at ${trade.stop:,.2f} — loss cut",
        )
        return

    if bar.high >= trade.target:
        _close_trade(
            sim, trade, day, _sell_price(trade.target, money),
            f"Hit the target at ${trade.target:,.2f} — profit taken",
        )
        return

    trade.peak = max(trade.peak, bar.close)


def _close_trade(
    sim: Sim, trade: SimTrade, day: date, price: float, reason: str
) -> None:
    trade.exit_day = day
    trade.exit_price = price
    trade.exit_reason = reason
    trade.costs += _commission(trade.shares, sim.money)
    sim.cash += trade.shares * price
    sim.cash -= _commission(trade.shares, sim.money)
    sim.trades.append(trade)
    sim.positions.pop(trade.symbol, None)


def apply_trailing_stop(trade: SimTrade, atr: float, trail_atr: float) -> None:
    """Ratchet the stop up behind the highest close. Never moves it down."""
    if trail_atr <= 0 or atr <= 0:
        return
    trade.stop = max(trade.stop, trade.peak - trail_atr * atr)


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


def _max_drawdown(curve: list[tuple[date, float]]) -> tuple[float, date | None]:
    """Worst peak-to-trough fall, as a percent. The number that decides whether
    you would actually have stuck with this."""
    peak = 0.0
    worst = 0.0
    worst_day: date | None = None
    for day, equity in curve:
        peak = max(peak, equity)
        if peak > 0:
            fall = (peak - equity) / peak * 100
            if fall > worst:
                worst, worst_day = fall, day
    return worst, worst_day


def _sharpe(curve: list[tuple[date, float]]) -> float | None:
    """Return per unit of wobble, annualised. Above 1 is respectable."""
    values = [v for _, v in curve]
    rets = [
        (b - a) / a for a, b in zip(values, values[1:]) if a > 0
    ]
    if len(rets) < 20:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var)
    if sd < 1e-9:
        return None
    return (mean / sd) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _buy_and_hold(
    charts: dict[str, Chart], starting_cash: float, m: Money, days: list[date]
) -> dict:
    """Split the money evenly, buy on day one, do nothing until the end.

    The number every strategy has to beat. If it does not, the honest answer
    is to stop trading and just own the stocks. Returned as a full daily curve
    so the chart can show both lines rather than only the final score.
    """
    first_day, last_day = days[0], days[-1]
    inside: dict[str, list[Bar]] = {}
    for symbol, c in charts.items():
        rows = [b for b in c.bars if first_day <= b.day <= last_day]
        if rows:
            inside[symbol] = rows
    if not inside:
        return {
            "final_value": round(starting_cash, 2),
            "return_percent": 0.0,
            "curve": [],
        }

    slice_cash = starting_cash / len(inside)
    shares: dict[str, float] = {}
    closes: dict[str, dict[date, float]] = {}
    for symbol, rows in inside.items():
        entry = _buy_price(rows[0].open, m)
        shares[symbol] = slice_cash / entry if entry else 0.0
        closes[symbol] = {b.day: b.close for b in rows}

    curve: list[dict] = []
    held: dict[str, float] = {s: rows[0].open for s, rows in inside.items()}
    for day in days:
        for symbol in inside:
            price = closes[symbol].get(day)
            if price is not None:
                held[symbol] = price  # otherwise carry yesterday's price forward
        value = sum(shares[s] * held[s] for s in inside)
        curve.append({"day": day.isoformat(), "value": round(value, 2)})

    total = sum(shares[s] * _sell_price(held[s], m) for s in inside)
    return {
        "final_value": round(total, 2),
        "return_percent": round(pct_change(starting_cash, total), 2),
        "curve": curve,
    }


def _report(
    sim: Sim, charts: dict[str, Chart], rules: Rules, money: Money, days: list[date]
) -> dict:
    trades = sim.trades
    final = sim.curve[-1][1] if sim.curve else money.starting_cash
    total_return = pct_change(money.starting_cash, final)

    wins = [t for t in trades if t.is_win]
    losses = [t for t in trades if not t.is_win]
    gross_win = sum(t.profit for t in wins)
    gross_loss = abs(sum(t.profit for t in losses))

    win_rate = len(wins) / len(trades) * 100 if trades else None
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    expectancy = sum(t.profit for t in trades) / len(trades) if trades else 0.0
    avg_hold = sum(t.held_days for t in trades) / len(trades) if trades else 0.0

    drawdown, drawdown_day = _max_drawdown(sim.curve)
    years = max((days[-1] - days[0]).days / 365.25, 1 / 365.25)
    cagr = ((final / money.starting_cash) ** (1 / years) - 1) * 100 if final > 0 else -100.0

    benchmark = _buy_and_hold(charts, money.starting_cash, money, days)
    # The curve rides alongside the result, not inside `stats` -- stats gets
    # saved to the database, and a few thousand points per saved run adds up.
    benchmark_curve = benchmark.pop("curve")
    beat_by = total_return - benchmark["return_percent"]

    stats = {
        "starting_cash": round(money.starting_cash, 2),
        "final_value": round(final, 2),
        "profit": round(final - money.starting_cash, 2),
        "return_percent": round(total_return, 2),
        "yearly_return_percent": round(cagr, 2),
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1) if win_rate is not None else None,
        "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "avg_hold_days": round(avg_hold, 1),
        "worst_drop_percent": round(drawdown, 2),
        "worst_drop_day": drawdown_day.isoformat() if drawdown_day else None,
        "sharpe": round(s, 2) if (s := _sharpe(sim.curve)) is not None else None,
        "years": round(years, 2),
        "first_day": days[0].isoformat(),
        "last_day": days[-1].isoformat(),
        "buy_and_hold": benchmark,
        "beat_buy_and_hold_by": round(beat_by, 2),
        "skipped_no_cash": sim.skipped_no_cash,
    }

    return {
        "stats": stats,
        "verdict": _verdict(stats),
        "plain_english": _summary(stats, rules),
        "equity_curve": [
            {"day": d.isoformat(), "value": round(v, 2)} for d, v in sim.curve
        ],
        "benchmark_curve": benchmark_curve,
        "trades": [
            {
                "symbol": t.symbol,
                "entry_day": t.entry_day.isoformat(),
                "entry_price": round(t.entry_price, 2),
                "exit_day": t.exit_day.isoformat() if t.exit_day else None,
                "exit_price": round(t.exit_price, 2) if t.exit_price else None,
                "shares": t.shares,
                "stop": round(t.stop, 2),
                "target": round(t.target, 2),
                "profit": round(t.profit, 2),
                "profit_percent": round(t.profit_percent, 2),
                "held_days": t.held_days,
                "reason": t.reason,
                "exit_reason": t.exit_reason,
                "stop_reason": t.stop_reason,
                "target_reason": t.target_reason,
                "win": t.is_win,
            }
            for t in sorted(trades, key=lambda x: x.entry_day)
        ],
        "notes": sim.notes[-40:],
        "per_symbol": _per_symbol(trades),
    }


def _per_symbol(trades: list[SimTrade]) -> list[dict]:
    grouped: dict[str, list[SimTrade]] = defaultdict(list)
    for t in trades:
        grouped[t.symbol].append(t)
    rows = []
    for symbol, group in grouped.items():
        wins = sum(1 for t in group if t.is_win)
        rows.append(
            {
                "symbol": symbol,
                "trades": len(group),
                "wins": wins,
                "win_rate": round(wins / len(group) * 100, 1),
                "profit": round(sum(t.profit for t in group), 2),
            }
        )
    return sorted(rows, key=lambda r: r["profit"], reverse=True)


def _verdict(s: dict) -> dict:
    """Grade the result, and say what to do about it.

    Deliberately harsh. A backtest exists to reject a strategy cheaply, so
    anything short of clearly good reads as a warning.
    """
    checks: list[dict] = []

    def check(name: str, value, target: str, status: str, note: str) -> None:
        checks.append(
            {"name": name, "value": value, "target": target,
             "status": status, "note": note}
        )

    if s["trades"] == 0:
        return {
            "grade": "no-trades",
            "headline": "This strategy never found a single trade.",
            "advice": "Loosen a rule — a lower momentum threshold, a shorter "
                      "breakout window, or the trend filter turned off — or test "
                      "a longer period or more stocks.",
            "checks": [],
        }

    if s["trades"] < MIN_TRADES_FOR_CONFIDENCE:
        check("Number of trades", s["trades"], f"{MIN_TRADES_FOR_CONFIDENCE}+", "warn",
              "Too few trades to trust the numbers. A good result here is "
              "probably luck.")
    else:
        check("Number of trades", s["trades"], f"{MIN_TRADES_FOR_CONFIDENCE}+", "good",
              "Enough trades for the numbers to mean something.")

    profit = s["profit"]
    check("Money made", f"${profit:,.0f}", "more than $0",
          "good" if profit > 0 else "bad",
          "Turned a profit after costs." if profit > 0
          else "Lost money. Nothing else matters until this is positive.")

    beat = s["beat_buy_and_hold_by"]
    hold = s["buy_and_hold"]["return_percent"]
    # Both figures side by side. A lone "-262%" reads like a catastrophic loss
    # rather than "came second by a wide margin", which is what it means.
    check("Beat buy-and-hold",
          f"{s['return_percent']:+.1f}% vs {hold:+.1f}%",
          "yours higher",
          "good" if beat > 0 else "bad",
          f"You made {s['return_percent']:+.1f}%; buying the same stocks on day "
          f"one and never touching them made {hold:+.1f}%. "
          + ("Worth the effort." if beat > 0
             else "All this trading left you behind — doing nothing would have "
                  "beaten it."))

    pf = s["profit_factor"]
    if pf is None:
        check("Profit factor", "—", "1.5+", "warn",
              "No losing trades yet, which on a small sample is luck, not skill.")
    else:
        check("Profit factor", pf, "1.5+",
              "good" if pf >= 1.5 else "warn" if pf >= 1.1 else "bad",
              f"For every $1 lost, ${pf:.2f} was made.")

    drop = s["worst_drop_percent"]
    check("Worst drop", f"{drop:.1f}%", "under 20%",
          "good" if drop < 15 else "warn" if drop < 25 else "bad",
          f"At the worst point the account was down {drop:.1f}% from its high. "
          f"Ask yourself honestly whether you would have kept going.")

    wr = s["win_rate"]
    if wr is not None:
        check("Win rate", f"{wr:.0f}%", "any", "info",
              "Win rate on its own means nothing — a 30% win rate with big "
              "winners beats a 70% one with big losers.")

    bad = sum(1 for c in checks if c["status"] == "bad")
    warn = sum(1 for c in checks if c["status"] == "warn")

    if bad:
        grade, headline, advice = (
            "stop",
            "Do not trade this yet.",
            "Something below is failing outright. Change one setting at a time "
            "and re-test — changing several at once tells you nothing about which "
            "one helped.",
        )
    elif warn:
        grade, headline, advice = (
            "caution",
            "Promising, but not proven.",
            "Test it over a longer period and on more stocks before trusting it. "
            "Then run it on paper for at least a month.",
        )
    else:
        grade, headline, advice = (
            "go",
            "This held up on past prices.",
            "Next step: run it on paper money for a month or two. Past prices "
            "are not a promise, and the only honest test is a live one where "
            "nothing is at stake.",
        )

    return {"grade": grade, "headline": headline, "advice": advice, "checks": checks}


def _summary(s: dict, rules: Rules) -> str:
    """One paragraph, no jargon."""
    if s["trades"] == 0:
        return (
            f"Over {s['years']:.1f} years this strategy found no trades at all. "
            f"Its rules were never all true on the same day."
        )
    direction = "grew" if s["profit"] >= 0 else "shrank"
    return (
        f"Starting with ${s['starting_cash']:,.0f}, this strategy made "
        f"{s['trades']} trades over {s['years']:.1f} years and {direction} the "
        f"account to ${s['final_value']:,.0f} — a {s['return_percent']:+.1f}% "
        f"change, or about {s['yearly_return_percent']:+.1f}% a year. "
        f"It won {s['wins']} times and lost {s['losses']}, holding each trade "
        f"about {s['avg_hold_days']:.0f} days. Along the way the account fell "
        f"{s['worst_drop_percent']:.1f}% from its high point. "
        f"Buying the same stocks on day one and never touching them would have "
        f"returned {s['buy_and_hold']['return_percent']:+.1f}%."
    )
