"""Trading state in SQLite, one file per environment (paper and live never share a ledger):
cycles, signals, regime, decisions with reasons, orders, fills, daily equity, protective stops,
the kill-switch latch, and the SimulatedBroker's ledger (sim_* tables)."""
import sqlite3
from pathlib import Path

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (cycle_id TEXT PRIMARY KEY, env TEXT, asset_class TEXT, slot TEXT, as_of TEXT,
    started_at TEXT, finished_at TEXT, status TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS signals (cycle_id TEXT, symbol TEXT, signal REAL, strategies TEXT);
CREATE TABLE IF NOT EXISTS regime (cycle_id TEXT, as_of TEXT, label TEXT, score INTEGER, cap REAL, reason TEXT);
CREATE TABLE IF NOT EXISTS decisions (cycle_id TEXT, as_of TEXT, asset_class TEXT, symbol TEXT, action TEXT,
    target_qty REAL, signal REAL, sentiment REAL, articles INTEGER, reason TEXT);
CREATE TABLE IF NOT EXISTS orders (client_order_id TEXT PRIMARY KEY, cycle_id TEXT, env TEXT, broker TEXT,
    asset_class TEXT, symbol TEXT, side TEXT, qty REAL, order_type TEXT, limit_price REAL, time_in_force TEXT,
    status TEXT, filled_qty REAL DEFAULT 0, avg_fill_price REAL, broker_order_id TEXT, reason TEXT,
    created_at TEXT, updated_at TEXT, error TEXT, dry_run INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS fills (client_order_id TEXT, symbol TEXT, side TEXT, qty REAL, price REAL, fee REAL,
    ts TEXT, UNIQUE (client_order_id, ts, qty, price));
CREATE TABLE IF NOT EXISTS equity_daily (date TEXT, asset_class TEXT, equity REAL, currency TEXT, fx_cad REAL,
    equity_cad REAL, gross_cad REAL, PRIMARY KEY (date, asset_class));
CREATE TABLE IF NOT EXISTS stops (broker TEXT, symbol TEXT, stop REAL, entry_ts TEXT, PRIMARY KEY (broker, symbol));
CREATE TABLE IF NOT EXISTS control (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS sim_orders (client_order_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, qty REAL,
    order_type TEXT, limit_price REAL, time_in_force TEXT, submitted_at TEXT, status TEXT,
    filled_qty REAL DEFAULT 0, avg_price REAL);
CREATE TABLE IF NOT EXISTS sim_fills (client_order_id TEXT, symbol TEXT, side TEXT, qty REAL, price REAL,
    fee REAL, ts TEXT);
CREATE TABLE IF NOT EXISTS sim_cash (amount REAL, ts TEXT, note TEXT);
"""
TERMINAL = ("filled", "canceled", "expired", "rejected", "rejected_by_risk", "dry_run", "not_submitted", "error")


def connect(path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit: every record lands before the next step
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert(conn, table: str, row: dict, replace: bool = False) -> None:
    verb = "INSERT OR REPLACE" if replace else "INSERT"
    conn.execute(f"{verb} INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                 [v.isoformat() if isinstance(v, pd.Timestamp) else v for v in row.values()])


def halted(conn) -> bool:
    row = conn.execute("SELECT value FROM control WHERE key = 'halted'").fetchone()
    return bool(row and row["value"] == "1")


def set_halted(conn, on: bool, now: pd.Timestamp) -> None:
    insert(conn, "control", {"key": "halted", "value": "1" if on else "0", "updated_at": now}, replace=True)
