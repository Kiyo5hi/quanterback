from __future__ import annotations

from datetime import date

from quanterback.adapters.risk.threshold_risk_gate import ThresholdRiskGate
from quanterback.domain.backtest import BacktestReport
from quanterback.domain.risk import RiskThresholds


def _report(**kw) -> BacktestReport:
    base = dict(
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
    base.update(kw)
    return BacktestReport(**base)


def test_all_thresholds_passed() -> None:
    a = ThresholdRiskGate().evaluate(_report(), RiskThresholds())
    assert a.passed
    assert a.failed_checks == []


def test_max_drawdown_failure_named() -> None:
    a = ThresholdRiskGate().evaluate(
        _report(max_drawdown=0.20), RiskThresholds(max_drawdown=0.10),
    )
    assert not a.passed
    assert "max_drawdown" in a.failed_checks


def test_multiple_failures_listed() -> None:
    a = ThresholdRiskGate().evaluate(
        _report(max_drawdown=0.20, sharpe=0.1, win_rate=0.20),
        RiskThresholds(max_drawdown=0.10, min_sharpe=0.5, min_win_rate=0.40),
    )
    assert not a.passed
    assert set(a.failed_checks) >= {"max_drawdown", "min_sharpe", "min_win_rate"}


def test_min_num_trades_failure_named() -> None:
    a = ThresholdRiskGate().evaluate(_report(num_trades=3), RiskThresholds(min_num_trades=10))
    assert not a.passed
    assert "min_num_trades" in a.failed_checks
