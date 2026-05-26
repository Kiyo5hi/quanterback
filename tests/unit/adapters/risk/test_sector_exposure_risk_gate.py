from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quanterback.adapters.risk.composite_risk_gate import CompositeRiskGate
from quanterback.adapters.risk.sector_exposure_risk_gate import SectorExposureRiskGate
from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.backtest import BacktestReport
from quanterback.domain.persisted import (
    PersistedBacktest,
    PersistedDecision,
    PersistedOrder,
    PersistedPosition,
    ScanRun,
)
from quanterback.domain.risk import RiskAssessment, RiskThresholds


@pytest.fixture()
def store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "t.sqlite")


def _now() -> datetime:
    return datetime(2026, 5, 22, tzinfo=timezone.utc)


@dataclass
class FakeExecutor:
    account_value: float = 100_000.0
    raise_on_account_value: bool = False

    def get_account_value(self) -> float:
        if self.raise_on_account_value:
            raise RuntimeError("alpaca down")
        return self.account_value


@dataclass
class FakeInnerGate:
    assessment: RiskAssessment
    calls: list = field(default_factory=list)

    def evaluate(self, report, thresholds):  # type: ignore[no-untyped-def]
        self.calls.append((report, thresholds))
        return self.assessment


def _seed_open_position(
    store: SqliteStore, ticker: str, qty: float, entry_price: float,
) -> None:
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
        ticker=ticker, order_id=oid, state="bracket_active",
        qty=qty, entry_price=entry_price, opened_at=_now(),
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
    # Account=100k, cap=10k/sector, new_max=5k → passes (no existing)
    inner = CompositeRiskGate()
    gate = SectorExposureRiskGate(
        inner=inner, store=store, executor=FakeExecutor(),
        max_sector_exposure_pct=0.10, position_size_pct=0.05,
    )
    a = gate.evaluate(_good_report("AMD"), RiskThresholds())
    assert a.passed


def test_reject_when_same_sector_exposure_exceeds(store: SqliteStore) -> None:
    # NVDA + ARM both ai_semi (same sector as AMD). Combined existing = 7k.
    # new_max = 5k. 7+5 > 10 → reject.
    _seed_open_position(store, "NVDA", qty=10, entry_price=400.0)  # 4k
    _seed_open_position(store, "ARM", qty=30, entry_price=100.0)   # 3k
    inner = CompositeRiskGate()
    gate = SectorExposureRiskGate(
        inner=inner, store=store, executor=FakeExecutor(),
        max_sector_exposure_pct=0.10, position_size_pct=0.05,
    )
    a = gate.evaluate(_good_report("AMD"), RiskThresholds())  # ai_semi
    assert not a.passed
    assert any("sector_exposure" in fc for fc in a.failed_checks)
    assert any(":ai_semi" in fc for fc in a.failed_checks)


def test_different_sector_existing_does_not_count(store: SqliteStore) -> None:
    # Big mega_tech positions don't count against the ai_semi cap
    _seed_open_position(store, "AAPL", qty=200, entry_price=200.0)   # 40k mega_tech
    _seed_open_position(store, "META", qty=100, entry_price=500.0)   # 50k mega_tech
    inner = CompositeRiskGate()
    gate = SectorExposureRiskGate(
        inner=inner, store=store, executor=FakeExecutor(),
        max_sector_exposure_pct=0.10, position_size_pct=0.05,
    )
    a = gate.evaluate(_good_report("AMD"), RiskThresholds())  # ai_semi
    assert a.passed


def test_same_sector_existing_counts(store: SqliteStore) -> None:
    # One large NVDA position (ai_semi) → 6k existing, +5k new max = 11k > 10k cap
    _seed_open_position(store, "NVDA", qty=20, entry_price=300.0)  # 6k
    inner = CompositeRiskGate()
    gate = SectorExposureRiskGate(
        inner=inner, store=store, executor=FakeExecutor(),
        max_sector_exposure_pct=0.10, position_size_pct=0.05,
    )
    a = gate.evaluate(_good_report("AMD"), RiskThresholds())  # ai_semi
    assert not a.passed
    assert any("sector_exposure" in fc for fc in a.failed_checks)


def test_pass_through_inner_rejection(store: SqliteStore) -> None:
    inner_rejection = RiskAssessment(
        passed=False, failed_checks=["sanity_max_drawdown"], size_multiplier=0.0,
    )
    fake_inner = FakeInnerGate(assessment=inner_rejection)
    gate = SectorExposureRiskGate(
        inner=fake_inner, store=store, executor=FakeExecutor(),
        max_sector_exposure_pct=0.10, position_size_pct=0.05,
    )
    a = gate.evaluate(_good_report("AMD"), RiskThresholds())
    assert not a.passed
    assert a.failed_checks == ["sanity_max_drawdown"]
    assert not any("sector_exposure" in fc for fc in a.failed_checks)


def test_fails_open_on_executor_error(store: SqliteStore) -> None:
    _seed_open_position(store, "NVDA", qty=100, entry_price=300.0)  # would otherwise reject
    inner = CompositeRiskGate()
    gate = SectorExposureRiskGate(
        inner=inner, store=store,
        executor=FakeExecutor(raise_on_account_value=True),
        max_sector_exposure_pct=0.10, position_size_pct=0.05,
    )
    a = gate.evaluate(_good_report("AMD"), RiskThresholds())
    assert a.passed
