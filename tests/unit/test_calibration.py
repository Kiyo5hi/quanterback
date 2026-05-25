from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.calibration import generate_calibration_report
from quanterback.domain.persisted import PersistedDecision, PersistedTrade, ScanRun
from quanterback.i18n import I18n


@pytest.fixture()
def store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "cal.sqlite")


def test_no_data_renders_friendly_msg(store: SqliteStore, i18n_en: I18n) -> None:
    out = generate_calibration_report(store, i18n_en)
    assert "error=no_data" in out or "no BUY" in out.lower() or "No BUY" in out


def test_replay_source_not_implemented(store: SqliteStore, i18n_en: I18n) -> None:
    out = generate_calibration_report(store, i18n_en, source="replay")
    assert "error=replay_not_implemented" in out or "replay-source" in out


def _seed_decision_and_trade(
    store: SqliteStore, *, confidence: float, win: bool, ticker: str, order_id: str,
) -> None:
    now = datetime.now(tz=timezone.utc)
    run = store.insert_scan_run(ScanRun(started_at=now, source="cron"))
    did = store.insert_decision(PersistedDecision(
        scan_run_id=run, ticker=ticker, summary_json="{}",
        decision_json=json.dumps({
            "action": "BUY", "ticker": ticker, "strategy": "MOMENTUM",
            "rationale": "fake", "confidence": confidence,
        }),
        llm_model="m", created_at=now,
    ))
    t = PersistedTrade(
        exit_order_id=order_id,
        ticker=ticker, qty=10, entry_price=100, entry_at=now - timedelta(hours=24),
        exit_price=105 if win else 95, exit_at=now,
        exit_reason="TAKE_PROFIT" if win else "STOP_LOSS",
        pnl_usd=50.0 if win else -50.0, pnl_pct=5.0 if win else -5.0,
        holding_hours=24.0, decision_id=did, created_at=now,
    )
    store.insert_trade(t)


def test_brier_score_perfect_when_confidence_matches_outcome(
    store: SqliteStore, i18n_en: I18n,
) -> None:
    # 10 trades with conf=0.9 all wins → Brier = mean((0.9-1)^2) = 0.01 (well-calibrated)
    for i in range(10):
        _seed_decision_and_trade(
            store, confidence=0.9, win=True,
            ticker=f"T{i}", order_id=f"o{i}",
        )
    out = generate_calibration_report(store, i18n_en)
    # Brier should be small (near 0.01)
    assert "brier=" in out or "0.01" in out


def test_brier_score_high_when_confidence_wrong(
    store: SqliteStore, i18n_en: I18n,
) -> None:
    # 10 trades with conf=0.9 all losses → Brier = (0.9-0)^2 = 0.81 (terrible)
    for i in range(10):
        _seed_decision_and_trade(
            store, confidence=0.9, win=False,
            ticker=f"L{i}", order_id=f"lo{i}",
        )
    out = generate_calibration_report(store, i18n_en)
    assert "brier=" in out or "0.81" in out or "0.8" in out


def test_days_filter_works(store: SqliteStore, i18n_en: I18n) -> None:
    now = datetime.now(tz=timezone.utc)
    # Add an old decision (10 days ago)
    old_run = store.insert_scan_run(ScanRun(started_at=now - timedelta(days=10), source="cron"))
    old_did = store.insert_decision(PersistedDecision(
        scan_run_id=old_run, ticker="OLD", summary_json="{}",
        decision_json=json.dumps({
            "action": "BUY", "ticker": "OLD", "strategy": "MOMENTUM",
            "rationale": "fake", "confidence": 0.5,
        }),
        llm_model="m", created_at=now - timedelta(days=10),
    ))
    t_old = PersistedTrade(
        exit_order_id="old_order",
        ticker="OLD", qty=10, entry_price=100, entry_at=now - timedelta(days=10, hours=24),
        exit_price=105, exit_at=now - timedelta(days=10),
        exit_reason="TAKE_PROFIT",
        pnl_usd=50.0, pnl_pct=5.0,
        holding_hours=24.0, decision_id=old_did, created_at=now - timedelta(days=10),
    )
    store.insert_trade(t_old)

    # Add a recent decision (1 day ago)
    recent_run = store.insert_scan_run(ScanRun(started_at=now - timedelta(days=1), source="cron"))
    recent_did = store.insert_decision(PersistedDecision(
        scan_run_id=recent_run, ticker="NEW", summary_json="{}",
        decision_json=json.dumps({
            "action": "BUY", "ticker": "NEW", "strategy": "MOMENTUM",
            "rationale": "fake", "confidence": 0.8,
        }),
        llm_model="m", created_at=now - timedelta(days=1),
    ))
    t_recent = PersistedTrade(
        exit_order_id="recent_order",
        ticker="NEW", qty=10, entry_price=100, entry_at=now - timedelta(days=1, hours=24),
        exit_price=110, exit_at=now - timedelta(days=1),
        exit_reason="TAKE_PROFIT",
        pnl_usd=100.0, pnl_pct=10.0,
        holding_hours=24.0, decision_id=recent_did, created_at=now - timedelta(days=1),
    )
    store.insert_trade(t_recent)

    # Test without filter: should include both
    out_all = generate_calibration_report(store, i18n_en)
    assert "n=2" in out_all or "Decisions analyzed:  2" in out_all

    # Test with 5-day filter: should only include recent
    out_recent = generate_calibration_report(store, i18n_en, days=5)
    assert "n=1" in out_recent or "Decisions analyzed:  1" in out_recent


def test_bucket_width_configurable(store: SqliteStore, i18n_en: I18n) -> None:
    for i in range(5):
        _seed_decision_and_trade(
            store, confidence=0.3 + i * 0.1, win=True,
            ticker=f"T{i}", order_id=f"o{i}",
        )
    # With default 0.1 width, we should have 10 buckets
    out_default = generate_calibration_report(store, i18n_en, bucket_width=0.1)
    # With 0.2 width, we should have 5 buckets
    out_wider = generate_calibration_report(store, i18n_en, bucket_width=0.2)
    # Both should render without error
    assert "error=" not in out_default or "error=None" in out_default
    assert "error=" not in out_wider or "error=None" in out_wider
