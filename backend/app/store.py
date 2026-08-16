"""A small SQLite file that remembers settings, the activity log, and results.

SQLite rather than Postgres because this app runs on one machine for one
person. There is no server to start, no password to set, and the whole database
is a single file you can copy, back up, or delete.

sqlite3 is synchronous, so each call runs on a worker thread.
"""

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    level   TEXT NOT NULL,
    message TEXT NOT NULL
);

-- One row per buy the bot decided to make, kept even after the position
-- closes, so the journal survives what the broker forgets.
CREATE TABLE IF NOT EXISTS journal (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at           TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    strategy     TEXT NOT NULL,
    shares       INTEGER NOT NULL,
    stop         REAL,
    target       REAL,
    reason       TEXT,
    order_id     TEXT,
    status       TEXT NOT NULL DEFAULT 'submitted',
    closed_at    TEXT,
    close_reason TEXT
);

CREATE TABLE IF NOT EXISTS backtests (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    label   TEXT NOT NULL,
    request TEXT NOT NULL,
    stats   TEXT NOT NULL
);

-- One row per scan, so you can look back at what was interesting last week
-- and check honestly whether the high scorers actually worked out.
CREATE TABLE IF NOT EXISTS scans (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at     TEXT NOT NULL,
    result TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS activity_at ON activity(at DESC);
CREATE INDEX IF NOT EXISTS journal_at  ON journal(at DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # -- generic helpers ------------------------------------------------- #

    def _write(self, sql: str, args: tuple = ()) -> int:
        with self._connect() as conn:
            cur = conn.execute(sql, args)
            return cur.lastrowid

    def _read(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    # -- settings --------------------------------------------------------- #

    async def get_setting(self, key: str, default=None):
        rows = await asyncio.to_thread(
            self._read, "SELECT value FROM settings WHERE key = ?", (key,)
        )
        if not rows:
            return default
        return json.loads(rows[0]["value"])

    async def set_setting(self, key: str, value) -> None:
        await asyncio.to_thread(
            self._write,
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    # -- activity log ------------------------------------------------------ #

    async def log(self, message: str, level: str = "info") -> None:
        await asyncio.to_thread(
            self._write,
            "INSERT INTO activity(at, level, message) VALUES(?, ?, ?)",
            (_now(), level, message),
        )

    async def recent_activity(self, limit: int = 100) -> list[dict]:
        return await asyncio.to_thread(
            self._read,
            "SELECT * FROM activity ORDER BY id DESC LIMIT ?",
            (limit,),
        )

    # -- journal ----------------------------------------------------------- #

    async def record_buy(
        self, symbol: str, strategy: str, shares: int, stop: float,
        target: float, reason: str, order_id: str | None,
    ) -> int:
        return await asyncio.to_thread(
            self._write,
            "INSERT INTO journal(at, symbol, strategy, shares, stop, target, "
            "reason, order_id) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (_now(), symbol, strategy, shares, stop, target, reason, order_id),
        )

    async def record_close(self, symbol: str, reason: str) -> None:
        await asyncio.to_thread(
            self._write,
            "UPDATE journal SET status='closed', closed_at=?, close_reason=? "
            "WHERE symbol=? AND status='submitted'",
            (_now(), reason, symbol),
        )

    async def journal(self, limit: int = 100) -> list[dict]:
        return await asyncio.to_thread(
            self._read, "SELECT * FROM journal ORDER BY id DESC LIMIT ?", (limit,)
        )

    async def open_journal_entries(self) -> list[dict]:
        return await asyncio.to_thread(
            self._read, "SELECT * FROM journal WHERE status='submitted' ORDER BY id"
        )

    # -- scans -------------------------------------------------------------- #

    async def save_scan(self, result: dict) -> int:
        return await asyncio.to_thread(
            self._write,
            "INSERT INTO scans(at, result) VALUES(?, ?)",
            (_now(), json.dumps(result)),
        )

    async def latest_scan(self) -> dict | None:
        rows = await asyncio.to_thread(
            self._read, "SELECT result FROM scans ORDER BY id DESC LIMIT 1"
        )
        return json.loads(rows[0]["result"]) if rows else None

    async def recent_scans(self, limit: int = 10) -> list[dict]:
        rows = await asyncio.to_thread(
            self._read,
            "SELECT id, at, result FROM scans ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        for r in rows:
            r["result"] = json.loads(r["result"])
        return rows

    # -- saved backtests ---------------------------------------------------- #

    async def save_backtest(self, label: str, request: dict, stats: dict) -> int:
        return await asyncio.to_thread(
            self._write,
            "INSERT INTO backtests(at, label, request, stats) VALUES(?, ?, ?, ?)",
            (_now(), label, json.dumps(request), json.dumps(stats)),
        )

    async def saved_backtests(self, limit: int = 25) -> list[dict]:
        rows = await asyncio.to_thread(
            self._read,
            "SELECT id, at, label, request, stats FROM backtests ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        for r in rows:
            r["request"] = json.loads(r["request"])
            r["stats"] = json.loads(r["stats"])
        return rows

    async def delete_backtest(self, backtest_id: int) -> None:
        await asyncio.to_thread(
            self._write, "DELETE FROM backtests WHERE id = ?", (backtest_id,)
        )
