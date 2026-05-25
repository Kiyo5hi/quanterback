from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quanterback.adapters.risk.composite_risk_gate import CompositeRiskGate
from quanterback.adapters.risk.sector_concurrency_risk_gate import (
    SectorConcurrencyRiskGate,
)
from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.backtest import BacktestReport
from quanterback.domain.persisted import (
    PersistedBacktest,
    PersistedDecision,
    PersistedOrder,
    PersistedPosition,
    ScanRun,
)
from quanterback.domain.risk import RiskThresholds


@pytest.fixture()
def store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "t.sqlite")


def _now() -> datetime:
    return datetime(2026, 5, 22, tzinfo=timezone.utc)


def _seed_open_position(store: SqliteStore, ticker: str) -> None:
    run = store.insert_scan_run(ScanRun(started_at=_now(), source="seed"))
    did = store.insert_decision(PersistedDecision(
        scan_run_id=run, ticker=ticker, summary_json="{}", decision_json="{}",
        llm_model="m", created_at=_now(),
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


def _good_report(ticker: str = "AMD") -> BacktestReport:
    return BacktestReport(
        ticker=ticker, strategy="MOMENTUM", params={},
        period_start=date(2023, 1, 1), period_end=date(2026, 1, 1),
        num_trades=30, win_rate=0.5, max_drawdown=0.15, sharpe=1.0,
        profit_factor=1.5, cumulative_return=0.30, avg_trade_return=0.01,
        avg_bars_held=10.0, trades=[],
        buy_and_hold_return=0.20, buy_and_hold_max_drawdown=0.25,
        excess_return=0.10, drawdown_ratio=0.60,
        oos_num_trades=10, oos_win_rate=0.55, oos_max_drawdown=0.12,
        oos_sharpe=1.2, oos_cumulative_return=0.15, oos_excess_return=0.05,
    )


def test_pass_when_sector_under_cap(store: SqliteStore) -> None:
    inner = CompositeRiskGate()
    gate = SectorConcurrencyRiskGate(inner=inner, store=store, max_per_sector=2)
    a = gate.evaluate(_good_report("AMD"), RiskThresholds())
    assert a.passed


def test_reject_when_sector_at_cap(store: SqliteStore) -> None:
    _seed_open_position(store, "NVDA")
    _seed_open_position(store, "ARM")  # both ai_semi
    inner = CompositeRiskGate()
    gate = SectorConcurrencyRiskGate(inner=inner, store=store, max_per_sector=2)
    a = gate.evaluate(_good_report("AMD"), RiskThresholds())
    assert not a.passed
    assert any("sector_concurrency" in fc for fc in a.failed_checks)


def test_different_sectors_dont_count(store: SqliteStore) -> None:
    _seed_open_position(store, "AAPL")   # mega_tech
    _seed_open_position(store, "META")   # mega_tech
    inner = CompositeRiskGate()
    gate = SectorConcurrencyRiskGate(inner=inner, store=store, max_per_sector=2)
    a = gate.evaluate(_good_report("AMD"), RiskThresholds())  # ai_semi
    assert a.passed


def test_passes_with_one_position_under_cap(store: SqliteStore) -> None:
    _seed_open_position(store, "NVDA")  # ai_semi
    inner = CompositeRiskGate()
    gate = SectorConcurrencyRiskGate(inner=inner, store=store, max_per_sector=2)
    a = gate.evaluate(_good_report("AMD"), RiskThresholds())  # ai_semi
    assert a.passed


def test_respects_custom_max_per_sector(store: SqliteStore) -> None:
    _seed_open_position(store, "NVDA")  # ai_semi
    inner = CompositeRiskGate()
    gate = SectorConcurrencyRiskGate(inner=inner, store=store, max_per_sector=1)
    a = gate.evaluate(_good_report("AMD"), RiskThresholds())  # ai_semi
    assert not a.passed
    assert any("sector_concurrency" in fc for fc in a.failed_checks)
