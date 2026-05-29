from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.persisted import (
    PersistedBacktest,
    PersistedDecision,
    PersistedNotification,
    PersistedOrder,
    PersistedPosition,
    PersistedUserTrigger,
    ScanRun,
)


@pytest.fixture()
def store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "test.sqlite")


def _now() -> datetime:
    return datetime(2026, 5, 22, 14, 30, tzinfo=timezone.utc)


def test_insert_scan_run_returns_id(store: SqliteStore) -> None:
    rid = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    assert rid >= 1


def test_update_scan_run_sets_ended(store: SqliteStore) -> None:
    run = ScanRun(started_at=_now(), source="cron")
    rid = store.insert_scan_run(run)
    run.id = rid
    run.ended_at = _now()
    run.tickers_processed = 7
    store.update_scan_run(run)
    rows = store._conn.execute(
        "SELECT ended_at, tickers_processed FROM scan_runs WHERE id=?", (rid,)
    ).fetchone()
    assert rows[0] is not None
    assert rows[1] == 7


def test_insert_decision_returns_id(store: SqliteStore) -> None:
    run_id = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    did = store.insert_decision(PersistedDecision(
        scan_run_id=run_id, ticker="AAPL",
        summary_json='{"x":1}', decision_json='{"action":"PASS"}',
        llm_model="claude-sonnet-4-6", created_at=_now(),
    ))
    assert did >= 1


def test_insert_backtest_links_to_decision(store: SqliteStore) -> None:
    run_id = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    did = store.insert_decision(PersistedDecision(
        scan_run_id=run_id, ticker="AAPL", summary_json="{}",
        decision_json="{}", llm_model="m", created_at=_now(),
    ))
    bid = store.insert_backtest(PersistedBacktest(
        decision_id=did, report_json="{}", passed=True, created_at=_now(),
    ))
    assert bid >= 1


def test_insert_order_links_decision_and_backtest(store: SqliteStore) -> None:
    run_id = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    did = store.insert_decision(PersistedDecision(
        scan_run_id=run_id, ticker="AAPL", summary_json="{}",
        decision_json="{}", llm_model="m", created_at=_now(),
    ))
    bid = store.insert_backtest(PersistedBacktest(
        decision_id=did, report_json="{}", passed=True, created_at=_now(),
    ))
    oid = store.insert_order(PersistedOrder(
        decision_id=did, backtest_id=bid, bracket_spec_json="{}",
        submitted_at=_now(),
    ))
    assert oid >= 1


def test_query_recent_decisions_ordered_desc(store: SqliteStore) -> None:
    run_id = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    for i in range(3):
        store.insert_decision(PersistedDecision(
            scan_run_id=run_id, ticker="AAPL", summary_json=str(i),
            decision_json="{}", llm_model="m",
            created_at=datetime(2026, 5, 22, 14, i, tzinfo=timezone.utc),
        ))
    recent = store.query_recent_decisions("AAPL", limit=2)
    assert len(recent) == 2
    # most recent first
    assert recent[0].summary_json == "2"


def test_insert_and_update_notification(store: SqliteStore) -> None:
    nid = store.insert_notification(PersistedNotification(
        event_kind="decision", payload_json="{}",
    ))
    assert nid >= 1
    store.update_notification(PersistedNotification(
        id=nid, event_kind="decision", payload_json="{}",
        sent_at=_now(), sent_ok=True,
    ))
    rows = store._conn.execute(
        "SELECT sent_ok FROM notifications WHERE id=?", (nid,)
    ).fetchone()
    assert rows[0] == 1


def test_query_pending_notifications_only_unsent(store: SqliteStore) -> None:
    nid_pending = store.insert_notification(PersistedNotification(
        event_kind="decision", payload_json="{}",
    ))
    nid_sent = store.insert_notification(PersistedNotification(
        event_kind="decision", payload_json="{}",
    ))
    store.update_notification(PersistedNotification(
        id=nid_sent, event_kind="decision", payload_json="{}",
        sent_at=_now(), sent_ok=True,
    ))
    pending = store.query_pending_notifications()
    ids = {p.id for p in pending}
    assert nid_pending in ids
    assert nid_sent not in ids


def _seeded_order(store: SqliteStore) -> int:
    run_id = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    did = store.insert_decision(PersistedDecision(
        scan_run_id=run_id, ticker="AAPL", summary_json="{}",
        decision_json="{}", llm_model="m", created_at=_now(),
    ))
    bid = store.insert_backtest(PersistedBacktest(
        decision_id=did, report_json="{}", passed=True, created_at=_now(),
    ))
    return store.insert_order(PersistedOrder(
        decision_id=did, backtest_id=bid, bracket_spec_json="{}",
        submitted_at=_now(),
    ))


def test_upsert_position_inserts_when_new(store: SqliteStore) -> None:
    oid = _seeded_order(store)
    pid = store.upsert_position(PersistedPosition(
        ticker="AAPL", order_id=oid, state="pending", opened_at=_now(),
    ))
    assert pid >= 1


def test_update_position_qty_after_trim(store: SqliteStore) -> None:
    """Bug 2 regression: after a partial trim the local qty must be reduced so
    the next scan trims against the real remaining size (not the stale qty),
    preventing repeated trims + re-buy churn."""
    oid = _seeded_order(store)
    store.upsert_position(PersistedPosition(
        ticker="AMD", order_id=oid, state="bracket_active", qty=54,
        entry_price=490.0, opened_at=_now(),
    ))
    rows = store.update_position_qty("AMD", 27)
    assert rows == 1
    pos = next(p for p in store.get_open_positions() if p.ticker == "AMD")
    assert pos.qty == 27
    assert pos.state == "bracket_active"


def test_second_pending_supersedes_first(store: SqliteStore) -> None:
    """Submitting a second order for same ticker auto-supersedes stale pending.

    Previously this raised IntegrityError, but Alpaca pending orders that never
    fill would block all subsequent submissions forever. Now the new submit
    closes the abandoned pending and proceeds.
    """
    oid = _seeded_order(store)
    pid1 = store.upsert_position(PersistedPosition(
        ticker="AAPL", order_id=oid, state="pending", opened_at=_now(),
    ))
    pid2 = store.upsert_position(PersistedPosition(
        ticker="AAPL", order_id=oid, state="pending", opened_at=_now(),
    ))
    assert pid2 > pid1
    # Old row marked closed with superseded reason; new row is the active pending
    rows = list(store._conn.execute(  # type: ignore[attr-defined]
        "SELECT state, exit_reason FROM positions WHERE ticker='AAPL' ORDER BY id"
    ))
    assert rows[0]["state"] == "closed"
    assert rows[0]["exit_reason"] == "superseded_by_new_submit"
    assert rows[1]["state"] == "pending"


def test_closed_position_does_not_block_new(store: SqliteStore) -> None:
    oid = _seeded_order(store)
    pid = store.upsert_position(PersistedPosition(
        ticker="AAPL", order_id=oid, state="pending", opened_at=_now(),
    ))
    store.upsert_position(PersistedPosition(
        id=pid, ticker="AAPL", order_id=oid, state="closed",
        opened_at=_now(), closed_at=_now(), exit_reason="manual",
    ))
    new_pid = store.upsert_position(PersistedPosition(
        ticker="AAPL", order_id=oid, state="pending", opened_at=_now(),
    ))
    assert new_pid > pid


def test_query_open_lifecycles_filters_closed(store: SqliteStore) -> None:
    oid = _seeded_order(store)
    store.upsert_position(PersistedPosition(
        ticker="AAPL", order_id=oid, state="bracket_active", opened_at=_now(),
    ))
    open_list = store.query_open_lifecycles()
    tickers = {lc.ticker for lc in open_list}
    assert "AAPL" in tickers
    assert all(lc.state != "closed" for lc in open_list)


def test_insert_user_trigger_returns_id(store: SqliteStore) -> None:
    tid_result = store.insert_user_trigger(PersistedUserTrigger(
        ticker="aapl", actor="42",
        requested_at=_now(),
    ))
    assert tid_result >= 1


def test_user_trigger_ticker_uppercased_on_insert(store: SqliteStore) -> None:
    _ = store.insert_user_trigger(PersistedUserTrigger(
        ticker="aapl", actor="42",
        requested_at=_now(),
    ))
    pending = store.query_pending_user_triggers()
    assert len(pending) == 1
    assert pending[0].ticker == "AAPL"  # uppercased on insert


def test_query_pending_user_triggers_only_pending(store: SqliteStore) -> None:
    _ = store.insert_user_trigger(PersistedUserTrigger(
        ticker="AAPL", actor="42", requested_at=_now(),
    ))
    tid_msft = store.insert_user_trigger(PersistedUserTrigger(
        ticker="MSFT", actor="42", requested_at=_now(),
    ))
    store.mark_user_trigger_processed(tid_msft)
    pending = store.query_pending_user_triggers()
    tickers = {t.ticker for t in pending}
    assert tickers == {"AAPL"}


def test_mark_user_trigger_processed_updates_state_and_time(store: SqliteStore) -> None:
    tid = store.insert_user_trigger(PersistedUserTrigger(
        ticker="AAPL", actor="42", requested_at=_now(),
    ))
    store.mark_user_trigger_processed(tid)
    row = store._conn.execute(
        "SELECT state, processed_at FROM user_triggers WHERE id=?", (tid,)
    ).fetchone()
    assert row["state"] == "processed"
    assert row["processed_at"] is not None


def test_add_watchlist_ticker_returns_true_on_success(store: SqliteStore) -> None:
    ok = store.add_watchlist_ticker("AAPL", source="user")
    assert ok is True
    entries = store.list_watchlist()
    assert len(entries) == 1
    assert entries[0].ticker == "AAPL"
    assert entries[0].source == "user"


def test_add_watchlist_ticker_returns_false_on_duplicate(store: SqliteStore) -> None:
    store.add_watchlist_ticker("AAPL", source="user")
    ok = store.add_watchlist_ticker("AAPL", source="config")
    assert ok is False
    entries = store.list_watchlist()
    assert len(entries) == 1


def test_add_watchlist_ticker_uppercase(store: SqliteStore) -> None:
    ok = store.add_watchlist_ticker("aapl", source="user")
    assert ok is True
    entries = store.list_watchlist()
    assert entries[0].ticker == "AAPL"


def test_remove_watchlist_ticker_returns_true_on_success(store: SqliteStore) -> None:
    store.add_watchlist_ticker("AAPL", source="user")
    ok = store.remove_watchlist_ticker("AAPL")
    assert ok is True
    entries = store.list_watchlist()
    assert len(entries) == 0


def test_remove_watchlist_ticker_returns_false_on_not_found(store: SqliteStore) -> None:
    ok = store.remove_watchlist_ticker("AAPL")
    assert ok is False


def test_remove_watchlist_ticker_protects_config_source(store: SqliteStore) -> None:
    store.add_watchlist_ticker("AAPL", source="config")
    ok = store.remove_watchlist_ticker("AAPL", force=False)
    assert ok is False
    entries = store.list_watchlist()
    assert len(entries) == 1


def test_remove_watchlist_ticker_force_overrides_protection(store: SqliteStore) -> None:
    store.add_watchlist_ticker("AAPL", source="config")
    ok = store.remove_watchlist_ticker("AAPL", force=True)
    assert ok is True
    entries = store.list_watchlist()
    assert len(entries) == 0


def test_list_watchlist_ordered_by_source_then_ticker(store: SqliteStore) -> None:
    store.add_watchlist_ticker("MSFT", source="user")
    store.add_watchlist_ticker("AAPL", source="config")
    store.add_watchlist_ticker("TSLA", source="auto")
    entries = store.list_watchlist()
    # Should be ordered: auto, config, user (by source name alphabetically)
    sources = [e.source for e in entries]
    tickers = [e.ticker for e in entries]
    # Verify they're sorted by source then ticker
    assert sources == ["auto", "config", "user"]
    assert tickers == ["TSLA", "AAPL", "MSFT"]


def test_set_watchlist_source(store: SqliteStore) -> None:
    store.add_watchlist_ticker("AAPL", source="user")
    store.set_watchlist_source("AAPL", "auto")
    entries = store.list_watchlist()
    assert entries[0].source == "auto"
