from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS scan_runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  source TEXT NOT NULL,
  trigger_label TEXT DEFAULT '',
  tickers_processed INTEGER NOT NULL DEFAULT 0,
  errors_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY,
  scan_run_id INTEGER NOT NULL REFERENCES scan_runs(id),
  ticker TEXT NOT NULL,
  summary_json TEXT NOT NULL,
  decision_json TEXT NOT NULL,
  llm_model TEXT NOT NULL,
  llm_usage_json TEXT,
  rejected_reason TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_ticker_created
  ON decisions(ticker, created_at);

CREATE TABLE IF NOT EXISTS backtests (
  id INTEGER PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  report_json TEXT NOT NULL,
  passed INTEGER NOT NULL,
  failed_checks TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  backtest_id INTEGER NOT NULL REFERENCES backtests(id),
  bracket_spec_json TEXT NOT NULL,
  alpaca_order_id TEXT,
  submitted_at TEXT NOT NULL,
  dry_run INTEGER NOT NULL DEFAULT 0,
  raw_response_json TEXT
);

CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY,
  ticker TEXT NOT NULL,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  state TEXT NOT NULL,
  entry_price REAL,
  sl REAL,
  tp REAL,
  qty INTEGER,
  opened_at TEXT NOT NULL,
  closed_at TEXT,
  exit_reason TEXT,
  decision_id INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_per_ticker
  ON positions(ticker) WHERE state != 'closed';

CREATE TABLE IF NOT EXISTS system_state (
  id INTEGER PRIMARY KEY,
  mode TEXT NOT NULL,
  reason TEXT,
  actor TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY,
  event_kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  sent_at TEXT,
  sent_ok INTEGER NOT NULL DEFAULT 0,
  retry_count INTEGER NOT NULL DEFAULT 0,
  error TEXT
);

CREATE TABLE IF NOT EXISTS user_triggers (
  id INTEGER PRIMARY KEY,
  ticker TEXT NOT NULL,
  actor TEXT NOT NULL,
  requested_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending',
  processed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_user_triggers_state
  ON user_triggers(state, requested_at);

CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  exit_order_id TEXT UNIQUE NOT NULL,
  ticker TEXT NOT NULL,
  side TEXT NOT NULL DEFAULT 'LONG',
  qty REAL NOT NULL,
  entry_price REAL NOT NULL,
  entry_at TEXT NOT NULL,
  exit_price REAL NOT NULL,
  exit_at TEXT NOT NULL,
  exit_reason TEXT NOT NULL,
  pnl_usd REAL NOT NULL,
  pnl_pct REAL NOT NULL,
  holding_hours REAL NOT NULL,
  decision_id INTEGER,
  notes TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_exit_at ON trades(exit_at);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);

CREATE TABLE IF NOT EXISTS watchlist (
  ticker TEXT PRIMARY KEY,
  source TEXT NOT NULL CHECK (source IN ('config', 'user', 'auto')),
  added_at TEXT NOT NULL,
  notes TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_watchlist_source ON watchlist(source);

CREATE TABLE IF NOT EXISTS position_management_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scan_run_id INTEGER NOT NULL REFERENCES scan_runs(id),
  ticker TEXT NOT NULL,
  action TEXT NOT NULL,
  new_sl_price REAL,
  new_qty_pct REAL,
  reasoning TEXT,
  confidence REAL,
  applied INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pmd_scan_run
  ON position_management_decisions(scan_run_id);
CREATE INDEX IF NOT EXISTS idx_pmd_ticker
  ON position_management_decisions(ticker);
"""


def apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate_add_decision_id_to_positions(conn)
    _migrate_add_agent_debate_to_decisions(conn)
    _migrate_add_trigger_label_to_scan_runs(conn)


def _migrate_add_decision_id_to_positions(conn: sqlite3.Connection) -> None:
    """Idempotent migration: add decision_id column to positions if missing."""
    cursor = conn.execute("PRAGMA table_info(positions)")
    columns = {row[1] for row in cursor.fetchall()}
    if "decision_id" not in columns:
        conn.execute("ALTER TABLE positions ADD COLUMN decision_id INTEGER")
        conn.commit()


def _migrate_add_agent_debate_to_decisions(conn: sqlite3.Connection) -> None:
    """Idempotent migration: add agent_debate_json column to decisions if missing."""
    cursor = conn.execute("PRAGMA table_info(decisions)")
    columns = {row[1] for row in cursor.fetchall()}
    if "agent_debate_json" not in columns:
        conn.execute("ALTER TABLE decisions ADD COLUMN agent_debate_json TEXT NULL")
        conn.commit()


def _migrate_add_trigger_label_to_scan_runs(conn: sqlite3.Connection) -> None:
    """Idempotent migration: add trigger_label column to scan_runs if missing."""
    cursor = conn.execute("PRAGMA table_info(scan_runs)")
    columns = {row[1] for row in cursor.fetchall()}
    if "trigger_label" not in columns:
        conn.execute("ALTER TABLE scan_runs ADD COLUMN trigger_label TEXT DEFAULT ''")
        conn.commit()


def seed_watchlist_from_config_file(
    conn: sqlite3.Connection, watchlist_path: Path
) -> int:
    """Seed watchlist table from config file if table is empty.

    Returns count of tickers seeded. Idempotent: only runs if watchlist is empty.
    """
    # Check if table already has entries
    count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
    if count > 0:
        return 0

    # Read from file
    if not watchlist_path.exists():
        return 0

    now = datetime.now(tz=timezone.utc).isoformat()
    seeded = 0
    for line in watchlist_path.read_text().splitlines():
        ticker = line.strip().upper()
        if not ticker or ticker.startswith("#"):
            continue
        try:
            conn.execute(
                "INSERT INTO watchlist (ticker, source, added_at, notes) "
                "VALUES (?, ?, ?, ?)",
                (ticker, "config", now, ""),
            )
            seeded += 1
        except sqlite3.IntegrityError:
            pass  # Skip duplicates
    conn.commit()
    return seeded
