from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quanterback.adapters.risk.composite_risk_gate import CompositeRiskGate
from quanterback.adapters.risk.total_exposure_risk_gate import TotalExposureRiskGate
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
    """Inner gate with a fixed canned assessment."""
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


def test_pass_when_under_cap(store: SqliteStore) -> None:
    # Account=100k, cap=30k, new max=5k, no existing → passes
    inner = CompositeRiskGate()
    gate = TotalExposureRiskGate(
        inner=inner, store=store, executor=FakeExecutor(),
        max_total_exposure_pct=0.30, position_size_pct=0.05,
    )
    a = gate.evaluate(_good_report("AMD"), RiskThresholds())
    assert a.passed


def test_reject_when_existing_plus_new_max_exceeds_cap(store: SqliteStore) -> None:
    # Account=100k, cap=30k, new_max=5k. Seed positions worth ~28k → 28+5 > 30 → reject
    _seed_open_position(store, "NVDA", qty=100, entry_price=280.0)
    inner = CompositeRiskGate()
    gate = TotalExposureRiskGate(
        inner=inner, store=store, executor=FakeExecutor(),
        max_total_exposure_pct=0.30, position_size_pct=0.05,
    )
    a = gate.evaluate(_good_report("AMD"), RiskThresholds())
    assert not a.passed
    assert "total_exposure_exceeded" in a.failed_checks
    assert a.size_multiplier == 0.0


def test_pass_through_inner_rejection(store: SqliteStore) -> None:
    """When inner gate rejects, this gate should pass that through unchanged
    (don't double-check, don't add our own failures)."""
    inner_rejection = RiskAssessment(
        passed=False, failed_checks=["sanity_max_drawdown"], size_multiplier=0.0,
    )
    fake_inner = FakeInnerGate(assessment=inner_rejection)
    # Even though we'd otherwise pass, inner rejection must come through
    gate = TotalExposureRiskGate(
        inner=fake_inner, store=store, executor=FakeExecutor(),
        max_total_exposure_pct=0.30, position_size_pct=0.05,
    )
    a = gate.evaluate(_good_report("AMD"), RiskThresholds())
    assert not a.passed
    assert a.failed_checks == ["sanity_max_drawdown"]
    assert "total_exposure_exceeded" not in a.failed_checks


def test_fails_open_on_executor_error(store: SqliteStore) -> None:
    """If get_account_value raises, gate must allow (log warning, not block)."""
    _seed_open_position(store, "NVDA", qty=100, entry_price=280.0)
    inner = CompositeRiskGate()
    gate = TotalExposureRiskGate(
        inner=inner, store=store,
        executor=FakeExecutor(raise_on_account_value=True),
        max_total_exposure_pct=0.30, position_size_pct=0.05,
    )
    a = gate.evaluate(_good_report("AMD"), RiskThresholds())
    assert a.passed


def test_exactly_at_cap_passes(store: SqliteStore) -> None:
    # Account=100k, cap=30k, new_max=5k, existing=25k → exactly 30k (not > cap) → pass
    _seed_open_position(store, "NVDA", qty=100, entry_price=250.0)  # = 25k
    inner = CompositeRiskGate()
    gate = TotalExposureRiskGate(
        inner=inner, store=store, executor=FakeExecutor(),
        max_total_exposure_pct=0.30, position_size_pct=0.05,
    )
    a = gate.evaluate(_good_report("AMD"), RiskThresholds())
    assert a.passed
