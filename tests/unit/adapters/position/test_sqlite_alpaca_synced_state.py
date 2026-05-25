from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanterback.adapters.position.sqlite_alpaca_synced_state import (
    SqliteAlpacaSyncedPositionState,
)
from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.persisted import (
    PersistedBacktest,
    PersistedDecision,
    PersistedOrder,
    PersistedPosition,
    ScanRun,
)


@pytest.fixture()
def store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "t.sqlite")


def _now() -> datetime:
    return datetime(2026, 5, 22, tzinfo=timezone.utc)


def _seed_active_position(store: SqliteStore, ticker: str) -> None:
    run = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    did = store.insert_decision(PersistedDecision(
        scan_run_id=run, ticker=ticker, summary_json="{}",
        decision_json="{}", llm_model="m", created_at=_now(),
    ))
    bid = store.insert_backtest(PersistedBacktest(
        decision_id=did, report_json="{}", passed=True, created_at=_now(),
    ))
    oid = store.insert_order(PersistedOrder(
        decision_id=did, backtest_id=bid, bracket_spec_json="{}",
        submitted_at=_now(),
    ))
    store.upsert_position(PersistedPosition(
        ticker=ticker, order_id=oid, state="bracket_active", opened_at=_now(),
    ))


def test_has_open_lifecycle_true_after_position(store: SqliteStore) -> None:
    _seed_active_position(store, "AAPL")
    svc = SqliteAlpacaSyncedPositionState(store, alpaca_synced=False)
    assert svc.has_open_lifecycle("AAPL") is True
    assert svc.has_open_lifecycle("MSFT") is False


def test_get_open_returns_lifecycle(store: SqliteStore) -> None:
    _seed_active_position(store, "AAPL")
    svc = SqliteAlpacaSyncedPositionState(store, alpaca_synced=False)
    lc = svc.get_open("AAPL")
    assert lc is not None
    assert lc.ticker == "AAPL"
    assert lc.state == "bracket_active"
