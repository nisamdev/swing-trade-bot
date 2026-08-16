"""The web API the app talks to.

Small on purpose: every route is a thin wrapper that reads or writes, and all
the thinking lives in app/. If a route grows an `if` about strategy logic, it
belongs in app/strategies.py instead.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.backtest import run_backtest
from app.engine import DEFAULT_CONFIG, Engine
from app.market import Market, MarketError
from app.models import Money, Rules
from app.store import Store
from app.strategies import describe_strategies
from config import settings

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("swing")

store = Store(Path(settings.database_path))
market: Market | None = None
engine: Engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    global market, engine
    try:
        market = Market(
            settings.alpaca_api_key,
            settings.alpaca_secret_key,
            paper=settings.alpaca_paper,
            feed=settings.alpaca_data_feed,
        )
        account = await market.account()
        log.info(
            "Connected to Alpaca (%s). Account value $%.2f",
            account["mode"], account["value"],
        )
        if not settings.alpaca_paper:
            log.warning("ALPACA_PAPER is false. This account trades REAL MONEY.")
    except MarketError as exc:
        # The app still starts, so the UI can explain what is wrong rather
        # than showing a blank page and a connection-refused error.
        market = None
        log.error("Alpaca is not connected: %s", exc)

    engine = Engine(market, store)
    engine.start()
    await store.log("App started.")
    try:
        yield
    finally:
        await engine.stop()


app = FastAPI(title="Swing", version="1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def need_market() -> Market:
    if market is None:
        raise HTTPException(
            503,
            "Alpaca is not connected. Add your paper-trading keys to the .env "
            "file at the project root and restart the app.",
        )
    return market


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #


@app.get("/api/health")
async def health():
    return {"ok": True, "connected": market is not None}


@app.get("/api/overview")
async def overview():
    """One call for the home screen, so it renders in a single round trip."""
    out: dict = {"status": await engine.status(), "error": None}
    if market is None:
        out["error"] = (
            "Alpaca is not connected. Add ALPACA_API_KEY and ALPACA_SECRET_KEY "
            "to the .env file at the project root, then restart."
        )
        return out
    try:
        account, positions, clock = await asyncio.gather(
            market.account(), market.positions(), market.market_status()
        )
        out.update(account=account, positions=positions, market=clock)
    except MarketError as exc:
        out["error"] = str(exc)
    out["activity"] = await store.recent_activity(12)
    out["last_round"] = engine.last_result
    return out


@app.get("/api/positions")
async def positions():
    try:
        return await need_market().positions()
    except MarketError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/orders")
async def orders():
    try:
        return await need_market().open_orders()
    except MarketError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/activity")
async def activity(limit: int = 100):
    return await store.recent_activity(min(limit, 500))


@app.get("/api/journal")
async def journal(limit: int = 100):
    return await store.journal(min(limit, 500))


# --------------------------------------------------------------------------- #
# Strategies and settings
# --------------------------------------------------------------------------- #


@app.get("/api/strategies")
async def strategies():
    return describe_strategies()


@app.get("/api/config")
async def get_config():
    return await engine.config()


class ConfigPatch(BaseModel):
    watchlist: list[str] | None = None
    rules: dict | None = None
    money: dict | None = None
    auto_trade: bool | None = None
    run_at: str | None = None
    scan_at: str | None = None
    scan_enabled: bool | None = None
    scan_universe_size: int | None = None


@app.put("/api/config")
async def put_config(patch: ConfigPatch):
    body = {k: v for k, v in patch.model_dump().items() if v is not None}
    try:
        # Round-trip through the dataclasses so a bad value is rejected here,
        # not at 09:40 tomorrow morning.
        merged = await engine.config()
        Rules(**{**merged["rules"], **(body.get("rules") or {})})
        Money(**{**merged["money"], **(body.get("money") or {})})
    except TypeError as exc:
        raise HTTPException(400, f"That setting is not one this app knows: {exc}") from exc

    updated = await engine.save_config(body)
    if "auto_trade" in body:
        await store.log(
            "Trading turned ON — the bot will now place real paper orders."
            if body["auto_trade"]
            else "Trading turned OFF — the bot will only suggest trades.",
            "action",
        )
    return updated


@app.post("/api/config/reset")
async def reset_config():
    await store.set_setting("config", DEFAULT_CONFIG)
    return await engine.config()


# --------------------------------------------------------------------------- #
# Backtest
# --------------------------------------------------------------------------- #


class BacktestRequest(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["SPY"])
    years: float = 5.0
    start: date | None = None
    end: date | None = None
    rules: dict = Field(default_factory=dict)
    money: dict = Field(default_factory=dict)
    save_as: str | None = None


@app.post("/api/backtest")
async def backtest(req: BacktestRequest):
    m = need_market()

    symbols = [s.strip().upper() for s in req.symbols if s.strip()][:20]
    if not symbols:
        raise HTTPException(400, "Add at least one stock symbol to test.")

    end = req.end or date.today()
    start = req.start or end - timedelta(days=int(req.years * 365.25))
    if start >= end:
        raise HTTPException(400, "The start date has to come before the end date.")

    saved = await engine.config()
    try:
        rules = Rules(**{**saved["rules"], **req.rules})
        money = Money(**{**saved["money"], **req.money})
    except TypeError as exc:
        raise HTTPException(400, f"Unknown setting: {exc}") from exc

    # Extra history so the 200-day average is already warm on day one of the
    # test window. Without it the first months are dead time, and the result
    # quietly understates how many trades the strategy would have found.
    warmup = max(rules.trend_days, rules.slow_days, 60) * 2
    try:
        bars = await m.daily_bars(symbols, start - timedelta(days=warmup), end)
    except MarketError as exc:
        raise HTTPException(502, str(exc)) from exc

    empty = [s for s in symbols if not bars.get(s)]
    if len(empty) == len(symbols):
        raise HTTPException(
            404,
            f"Alpaca returned no prices for {', '.join(empty)}. "
            f"Check the symbols are spelled right and are US-listed stocks.",
        )

    # The warm-up bars stay attached so the averages are already defined on day
    # one; `trade_from` stops any trade happening before the window asked for.
    full = {s: rows for s, rows in bars.items() if rows}

    try:
        result = await asyncio.to_thread(run_backtest, full, rules, money, start)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    result["request"] = {
        "symbols": symbols,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "strategy": rules.strategy,
        "rules": rules.__dict__,
        "money": money.__dict__,
        "skipped_symbols": empty,
    }

    if req.save_as:
        result["saved_id"] = await store.save_backtest(
            req.save_as, result["request"], result["stats"]
        )
    return result


@app.get("/api/backtests")
async def saved_backtests():
    return await store.saved_backtests()


@app.delete("/api/backtests/{backtest_id}")
async def delete_backtest(backtest_id: int):
    await store.delete_backtest(backtest_id)
    return {"deleted": backtest_id}


@app.get("/api/prices/{symbol}")
async def prices(symbol: str, days: int = 260):
    """Daily closes plus the averages, for the chart on the stock page."""
    m = need_market()
    cfg = await engine.config()
    rules = Rules(**cfg["rules"])
    # Fetch enough warm-up for the longest average, not just the window being
    # charted, or every indicator comes back empty on a short view.
    needed = max(days, rules.trend_days, rules.slow_days) + 60
    try:
        bars = await m.daily_bars(
            [symbol], date.today() - timedelta(days=int(needed * 1.6))
        )
    except MarketError as exc:
        raise HTTPException(502, str(exc)) from exc

    rows = bars.get(symbol.upper()) or []
    if not rows:
        raise HTTPException(404, f"No prices came back for {symbol.upper()}.")

    from app.strategies import build_chart, get_strategy, plan_exits

    chart = build_chart(rows, rules)
    i = len(rows) - 1
    verdict = get_strategy(rules.strategy).entry(chart, i)

    cut = max(0, len(rows) - days)
    plan = plan_exits(chart, i, chart.close[i])
    overlay = chart.landscape.visible(i, chart.close[i])

    return {
        "symbol": symbol.upper(),
        "today": {
            "buy": verdict.buy,
            "reason": verdict.reason,
            "price": round(chart.close[i], 2),
            "as_of": rows[i].day.isoformat(),
            "stop": round(plan.stop, 2),
            "target": round(plan.target, 2),
            "stop_reason": plan.stop_reason,
            "target_reason": plan.target_reason,
            "reward_risk": round(plan.reward_risk(chart.close[i]), 2),
        },
        # Support/resistance lines and supply/demand bands near today's price.
        "levels": overlay["levels"],
        "zones": overlay["zones"],
        "averages": {"trend": rules.trend_days, "ema_fast": rules.fast_ema_days},
        "bars": [
            {
                "day": b.day.isoformat(),
                "close": round(b.close, 2),
                "high": round(b.high, 2),
                "low": round(b.low, 2),
                "volume": b.volume,
                "trend": round(t, 2) if (t := chart.trend[n]) is not None else None,
                "ema_fast": (
                    round(e, 2) if (e := chart.ema_fast[n]) is not None else None
                ),
                "rsi": round(r, 1) if (r := chart.rsi[n]) is not None else None,
            }
            for n, b in enumerate(rows)
            if n >= cut
        ],
    }


# --------------------------------------------------------------------------- #
# The bot
# --------------------------------------------------------------------------- #


@app.get("/api/bot")
async def bot_status():
    return await engine.status()


@app.post("/api/bot/check-now")
async def check_now():
    try:
        return await engine.run_round(triggered_by="you")
    except MarketError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/ideas")
async def ideas():
    """The latest scan. Served from storage so opening the page is instant."""
    saved = engine.last_ideas or await store.latest_scan()
    return saved or {"ideas": [], "looked_at": 0, "at": None, "rejected": []}


@app.post("/api/ideas/scan")
async def run_scan():
    try:
        return await engine.run_scan(triggered_by="you")
    except MarketError as exc:
        raise HTTPException(502, str(exc)) from exc


class HandBuy(BaseModel):
    # Leave empty to let the risk setting decide the share count.
    shares: int | None = None


@app.get("/api/bot/preview-buy/{symbol}")
async def preview_buy(symbol: str):
    try:
        return await engine.preview_buy(symbol)
    except MarketError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/bot/buy/{symbol}")
async def buy_by_hand(symbol: str, body: HandBuy | None = None):
    try:
        return await engine.buy_by_hand(symbol, body.shares if body else None)
    except MarketError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/bot/sell/{symbol}")
async def sell_now(symbol: str):
    m = need_market()
    try:
        order = await m.sell_everything_in(symbol.upper())
    except MarketError as exc:
        raise HTTPException(502, str(exc)) from exc
    await store.record_close(symbol.upper(), "Sold by hand")
    await store.log(f"You sold {symbol.upper()} by hand.", "action")
    return order or {"message": f"You were not holding {symbol.upper()}."}


@app.post("/api/bot/cancel-orders")
async def cancel_orders():
    m = need_market()
    try:
        count = await m.cancel_orders()
    except MarketError as exc:
        raise HTTPException(502, str(exc)) from exc
    await store.log(f"Cancelled {count} pending order(s).", "action")
    return {"cancelled": count}
