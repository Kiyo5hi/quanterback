"""Tests for WatchlistAutoManager."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from quanterback.adapters.lifecycle.watchlist_auto_manager import (
    WatchlistAutoManager,
)
from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.persisted import (
    PersistedDecision,
    ScanRun,
)


@pytest.fixture()
def store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "test.sqlite")


def _now() -> datetime:
    return datetime(2026, 5, 22, 14, 30, tzinfo=timezone.utc)


def test_promotes_ticker_with_enough_buys(store: SqliteStore) -> None:
    """Ticker with ≥3 BUY decisions in last 7 days should be promoted."""
    manager = WatchlistAutoManager(store=store, promote_min_buys=3, enabled=True)

    # Insert 3 BUY decisions for AAPL within the window
    run_id = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    for i in range(3):
        decision_json = json.dumps({"action": "BUY"})
        store.insert_decision(PersistedDecision(
            scan_run_id=run_id, ticker="AAPL",
            summary_json="{}", decision_json=decision_json,
            llm_model="m",
            created_at=_now() - timedelta(days=i),
        ))

    # Run the manager
    counts = manager.tick()

    assert counts["promoted"] == 1
    entries = store.list_watchlist()
    assert len(entries) == 1
    assert entries[0].ticker == "AAPL"
    assert entries[0].source == "auto"


def test_does_not_promote_below_threshold(store: SqliteStore) -> None:
    """Ticker with <3 BUY decisions should not be promoted."""
    manager = WatchlistAutoManager(store=store, promote_min_buys=3, enabled=True)

    # Insert only 2 BUY decisions for AAPL
    run_id = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    for i in range(2):
        decision_json = json.dumps({"action": "BUY"})
        store.insert_decision(PersistedDecision(
            scan_run_id=run_id, ticker="AAPL",
            summary_json="{}", decision_json=decision_json,
            llm_model="m",
            created_at=_now() - timedelta(days=i),
        ))

    # Run the manager
    counts = manager.tick()

    assert counts["promoted"] == 0
    entries = store.list_watchlist()
    assert len(entries) == 0


def test_respects_promote_window(store: SqliteStore) -> None:
    """Only decisions within the window should count."""
    manager = WatchlistAutoManager(
        store=store, promote_min_buys=2, promote_window_days=7, enabled=True
    )

    # Insert 2 BUY decisions: 1 within window, 1 outside
    run_id = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    decision_json = json.dumps({"action": "BUY"})
    store.insert_decision(PersistedDecision(
        scan_run_id=run_id, ticker="AAPL",
        summary_json="{}", decision_json=decision_json,
        llm_model="m",
        created_at=_now() - timedelta(days=2),
    ))
    store.insert_decision(PersistedDecision(
        scan_run_id=run_id, ticker="AAPL",
        summary_json="{}", decision_json=decision_json,
        llm_model="m",
        created_at=_now() - timedelta(days=8),  # Outside 7-day window
    ))

    counts = manager.tick()

    # Only the in-window decision counts
    assert counts["promoted"] == 0


def test_demotes_auto_ticker_with_no_recent_buys(store: SqliteStore) -> None:
    """Auto-source ticker with no BUY in the demote window should be removed."""
    manager = WatchlistAutoManager(
        store=store, demote_max_quiet_days=14, enabled=True
    )

    # Add an auto-source ticker to the watchlist
    store.add_watchlist_ticker("AAPL", source="auto")

    # Insert an old BUY decision (outside the demote window)
    run_id = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    decision_json = json.dumps({"action": "BUY"})
    store.insert_decision(PersistedDecision(
        scan_run_id=run_id, ticker="AAPL",
        summary_json="{}", decision_json=decision_json,
        llm_model="m",
        created_at=_now() - timedelta(days=16),  # Outside 14-day window
    ))

    counts = manager.tick()

    assert counts["demoted"] == 1
    entries = store.list_watchlist()
    assert len(entries) == 0


def test_does_not_demote_user_or_config_ticker(store: SqliteStore) -> None:
    """User and config source tickers should not be auto-demoted."""
    manager = WatchlistAutoManager(
        store=store, demote_max_quiet_days=14, enabled=True
    )

    # Add both user and config source tickers
    store.add_watchlist_ticker("AAPL", source="user")
    store.add_watchlist_ticker("MSFT", source="config")

    # No BUY decisions for them
    counts = manager.tick()

    assert counts["demoted"] == 0
    entries = store.list_watchlist()
    assert len(entries) == 2


def test_does_not_demote_auto_with_recent_buy(store: SqliteStore) -> None:
    """Auto-source ticker with recent BUY should not be demoted."""
    manager = WatchlistAutoManager(
        store=store, demote_max_quiet_days=14, enabled=True
    )

    # Add an auto-source ticker
    store.add_watchlist_ticker("AAPL", source="auto")

    # Insert a recent BUY
    run_id = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    decision_json = json.dumps({"action": "BUY"})
    store.insert_decision(PersistedDecision(
        scan_run_id=run_id, ticker="AAPL",
        summary_json="{}", decision_json=decision_json,
        llm_model="m",
        created_at=_now() - timedelta(days=5),
    ))

    counts = manager.tick()

    assert counts["demoted"] == 0
    entries = store.list_watchlist()
    assert len(entries) == 1


def test_disabled_manager_returns_zero_counts(store: SqliteStore) -> None:
    """Disabled manager should do nothing."""
    manager = WatchlistAutoManager(store=store, enabled=False)

    # Add a ticker
    store.add_watchlist_ticker("AAPL", source="auto")

    counts = manager.tick()

    assert counts == {"promoted": 0, "demoted": 0}
    entries = store.list_watchlist()
    assert len(entries) == 1  # Still there
