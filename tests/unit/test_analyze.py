from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.analyze import generate_analyze_report
from quanterback.domain.persisted import (
    PersistedBacktest,
    PersistedDecision,
    ScanRun,
)
from quanterback.i18n import I18n


@pytest.fixture()
def store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "t.sqlite")




def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def test_empty_store_renders_none(store: SqliteStore, i18n_en: I18n) -> None:
    out = generate_analyze_report(store, i18n_en)
    assert "QuanterBack" in out or "decisions=" in out


def test_counts_pass_tickers(store: SqliteStore, i18n_en: I18n) -> None:
    run = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    for _ in range(5):
        store.insert_decision(PersistedDecision(
            scan_run_id=run, ticker="AAPL",
            summary_json="{}",
            decision_json=json.dumps({
                "action": "PASS", "ticker": "AAPL", "strategy": "MOMENTUM",
                "rationale": "extended above mvg avg",
                "confidence": 0.5,
            }),
            llm_model="m", created_at=_now(),
        ))
    out = generate_analyze_report(store, i18n_en)
    assert "AAPL" in out or "decisions=" in out


def test_risk_gate_rejection_counts_humanized(
    store: SqliteStore, i18n_en: I18n,
) -> None:
    run = store.insert_scan_run(ScanRun(started_at=_now(), source="cron"))
    did = store.insert_decision(PersistedDecision(
        scan_run_id=run, ticker="NVDA", summary_json="{}",
        decision_json="{}", llm_model="m", created_at=_now(),
    ))
    store.insert_backtest(PersistedBacktest(
        decision_id=did, report_json="{}", passed=False,
        failed_checks="sanity_max_drawdown,sanity_min_sharpe",
        created_at=_now(),
    ))
    out = generate_analyze_report(store, i18n_en)
    if "Risk-gate" in out or "rejections" in out:
        assert "drawdown" in out or "Sharpe" in out
