"""SQLite persistence for the paper portfolio."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT NOT NULL,
    symbol TEXT NOT NULL,
    pair_address TEXT,
    qty REAL NOT NULL,
    entry_price REAL NOT NULL,
    entry_usd REAL NOT NULL,
    peak_price REAL NOT NULL,
    opened_at REAL NOT NULL,
    closed_at REAL,
    close_price REAL,
    close_usd REAL,
    pnl_usd REAL,
    status TEXT NOT NULL DEFAULT 'open',
    thesis TEXT
);

CREATE INDEX IF NOT EXISTS idx_positions_status ON positions (status);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    token_address TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    usd REAL NOT NULL,
    fee_usd REAL NOT NULL,
    reason TEXT,
    pnl_usd REAL
);

CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades (ts);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    token_address TEXT,
    symbol TEXT,
    stage TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target), timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    connection.executescript(SCHEMA)
    connection.commit()
    return connection


def get_state(connection: sqlite3.Connection) -> dict[str, str]:
    return {row["key"]: row["value"] for row in connection.execute("SELECT key, value FROM state")}


def set_state(connection: sqlite3.Connection, values: dict) -> None:
    for key, value in values.items():
        connection.execute(
            "INSERT INTO state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
    connection.commit()


def bootstrap(connection: sqlite3.Connection, starting_cash: float, today: str) -> dict[str, str]:
    state = get_state(connection)
    if "cash_usd" not in state:
        set_state(
            connection,
            {
                "starting_cash_usd": f"{starting_cash:.6f}",
                "cash_usd": f"{starting_cash:.6f}",
                "day": today,
                "realized_pnl_today": "0",
                "realized_pnl_total": "0",
                "trades_today": "0",
                "created_at": f"{time.time():.0f}",
            },
        )
        state = get_state(connection)
    return state


def rollover_day(connection: sqlite3.Connection, today: str) -> dict[str, str]:
    state = get_state(connection)
    if state.get("day") != today:
        set_state(connection, {"day": today, "realized_pnl_today": "0", "trades_today": "0"})
        state = get_state(connection)
    return state


def cash(connection: sqlite3.Connection) -> float:
    return float(get_state(connection).get("cash_usd") or 0.0)


def open_positions(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute("SELECT * FROM positions WHERE status = 'open' ORDER BY opened_at").fetchall()
    return [dict(row) for row in rows]


def closed_positions(connection: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = connection.execute(
        "SELECT * FROM positions WHERE status = 'closed' ORDER BY closed_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]


def insert_position(
    connection: sqlite3.Connection,
    token_address: str,
    symbol: str,
    pair_address: str | None,
    qty: float,
    entry_price: float,
    entry_usd: float,
    thesis: str = "",
) -> int:
    cursor = connection.execute(
        "INSERT INTO positions "
        "(token_address, symbol, pair_address, qty, entry_price, entry_usd, peak_price, opened_at, status, thesis) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
        (token_address, symbol, pair_address, qty, entry_price, entry_usd, entry_price, time.time(), thesis),
    )
    connection.commit()
    return int(cursor.lastrowid)


def close_position(
    connection: sqlite3.Connection,
    position_id: int,
    close_price: float,
    close_usd: float,
    pnl_usd: float,
) -> None:
    connection.execute(
        "UPDATE positions SET status = 'closed', closed_at = ?, close_price = ?, close_usd = ?, pnl_usd = ? "
        "WHERE id = ? AND status = 'open'",
        (time.time(), close_price, close_usd, pnl_usd, position_id),
    )
    connection.commit()


def update_peak(connection: sqlite3.Connection, position_id: int, peak_price: float) -> None:
    connection.execute("UPDATE positions SET peak_price = ? WHERE id = ?", (peak_price, position_id))
    connection.commit()


def add_trade(
    connection: sqlite3.Connection,
    token_address: str,
    symbol: str,
    side: str,
    qty: float,
    price: float,
    usd: float,
    fee_usd: float,
    reason: str = "",
    pnl_usd: float | None = None,
) -> int:
    cursor = connection.execute(
        "INSERT INTO trades (ts, token_address, symbol, side, qty, price, usd, fee_usd, reason, pnl_usd) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (time.time(), token_address, symbol, side, qty, price, usd, fee_usd, reason, pnl_usd),
    )
    connection.commit()
    return int(cursor.lastrowid)


def recent_trades(connection: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = connection.execute("SELECT * FROM trades ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in rows]


def recent_token_activity(connection: sqlite3.Connection, since_ts: float) -> dict[str, float]:
    rows = connection.execute(
        "SELECT token_address, MAX(ts) AS last_ts FROM trades WHERE ts >= ? GROUP BY token_address",
        (since_ts,),
    ).fetchall()
    return {row["token_address"]: float(row["last_ts"]) for row in rows}


def record_decision(
    connection: sqlite3.Connection,
    stage: str,
    payload: dict,
    token_address: str | None = None,
    symbol: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO decisions (ts, token_address, symbol, stage, payload) VALUES (?, ?, ?, ?, ?)",
        (time.time(), token_address, symbol, stage, json.dumps(payload, default=str)[:8000]),
    )
    connection.commit()


def totals(connection: sqlite3.Connection) -> dict:
    state = get_state(connection)
    realized = connection.execute("SELECT COALESCE(SUM(pnl_usd), 0) AS pnl FROM trades WHERE pnl_usd IS NOT NULL").fetchone()
    closed = connection.execute("SELECT COUNT(*) AS n FROM positions WHERE status = 'closed'").fetchone()
    return {
        "starting_cash_usd": float(state.get("starting_cash_usd") or 0),
        "cash_usd": float(state.get("cash_usd") or 0),
        "realized_pnl_total": float(realized["pnl"] or 0),
        "closed_trades": int(closed["n"] or 0),
        "day": state.get("day"),
        "trades_today": int(float(state.get("trades_today") or 0)),
        "realized_pnl_today": float(state.get("realized_pnl_today") or 0),
    }
