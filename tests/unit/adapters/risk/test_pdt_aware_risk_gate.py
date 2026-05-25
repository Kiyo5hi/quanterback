from __future__ import annotations

from datetime import date

from quanterback.adapters.risk.pdt_aware_risk_gate import PdtAwareRiskGate
from quanterback.adapters.risk.threshold_risk_gate import ThresholdRiskGate
from quanterback.domain.backtest import BacktestReport
from quanterback.domain.risk import RiskThresholds
from tests.fakes.executor import InMemorySimulatorExecutor


def _good_report() -> BacktestReport:
    return BacktestReport(
        ticker="AAPL", strategy="MOMENTUM", params={},
        period_start=date(2023, 1, 1), period_end=date(2026, 1, 1),
        num_trades=50, win_rate=0.5, max_drawdown=0.05, sharpe=1.0,
        profit_factor=1.5, cumulative_return=0.2, avg_trade_return=0.005,
        avg_bars_held=10.0, trades=[],
        buy_and_hold_return=0.15, buy_and_hold_max_drawdown=0.10,
        excess_return=0.05, drawdown_ratio=0.5,
        oos_num_trades=20, oos_win_rate=0.5, oos_max_drawdown=0.05,
        oos_sharpe=1.0, oos_cumulative_return=0.1, oos_excess_return=0.0,
    )


def test_passes_when_inner_passes_and_account_clean() -> None:
    inner = ThresholdRiskGate()
    executor = InMemorySimulatorExecutor(account_value=10_000, day_trade_count=1)
    gate = PdtAwareRiskGate(inner=inner, executor=executor,
                             min_equity=25_000, max_day_trades=3)
    a = gate.evaluate(_good_report(), RiskThresholds())
    assert a.passed
    assert a.failed_checks == []


def test_rejects_when_below_equity_and_at_max_day_trades() -> None:
    inner = ThresholdRiskGate()
    executor = InMemorySimulatorExecutor(account_value=10_000, day_trade_count=3)
    gate = PdtAwareRiskGate(inner=inner, executor=executor,
                             min_equity=25_000, max_day_trades=3)
    a = gate.evaluate(_good_report(), RiskThresholds())
    assert not a.passed
    assert "pdt_protection" in a.failed_checks


def test_does_not_block_when_account_above_25k() -> None:
    inner = ThresholdRiskGate()
    executor = InMemorySimulatorExecutor(account_value=30_000, day_trade_count=5)
    gate = PdtAwareRiskGate(inner=inner, executor=executor,
                             min_equity=25_000, max_day_trades=3)
    a = gate.evaluate(_good_report(), RiskThresholds())
    assert a.passed


def test_inner_failures_passed_through_without_pdt_check() -> None:
    # Construct a report that fails threshold gate (low sharpe)
    bad = _good_report()
    bad_dict = bad.model_dump()
    bad_dict["sharpe"] = -1.0   # below 0.5 min_sharpe
    bad = BacktestReport(**bad_dict)
    inner = ThresholdRiskGate()
    executor = InMemorySimulatorExecutor(account_value=10_000, day_trade_count=3)
    gate = PdtAwareRiskGate(inner=inner, executor=executor)
    a = gate.evaluate(bad, RiskThresholds(min_sharpe=0.5))
    assert not a.passed
    assert "min_sharpe" in a.failed_checks
    # Should NOT have called account methods (no pdt_protection in list)
    assert "pdt_protection" not in a.failed_checks


def test_handles_executor_exception_gracefully() -> None:
    class BoomExecutor:
        def submit(self, *a, **kw): raise NotImplementedError
        def get_account_value(self) -> float:
            raise RuntimeError("alpaca down")
        def get_day_trade_count(self) -> int:
            raise RuntimeError("alpaca down")

    inner = ThresholdRiskGate()
    gate = PdtAwareRiskGate(inner=inner, executor=BoomExecutor())  # type: ignore[arg-type]
    a = gate.evaluate(_good_report(), RiskThresholds())
    # Conservative: pass through inner result rather than rejecting
    assert a.passed
