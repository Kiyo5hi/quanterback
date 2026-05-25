from __future__ import annotations

import sqlite3
from pathlib import Path

from quanterback.adapters.store.schema import apply_schema


def test_apply_schema_creates_all_tables(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    try:
        apply_schema(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    names = {r[0] for r in rows}
    expected = {
        "scan_runs", "decisions", "backtests", "orders",
        "positions", "system_state", "notifications",
    }
    assert expected.issubset(names)


def test_apply_schema_enables_wal(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    try:
        apply_schema(conn)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert mode.lower() == "wal"


def test_apply_schema_creates_unique_active_position_index(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    try:
        apply_schema(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    finally:
        conn.close()
    assert any(r[0] == "idx_one_active_per_ticker" for r in rows)


def test_apply_schema_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    try:
        apply_schema(conn)
        apply_schema(conn)  # second time must not raise
    finally:
        conn.close()
