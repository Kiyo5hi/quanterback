from __future__ import annotations

from datetime import date

from quanterback.adapters.risk.composite_risk_gate import CompositeRiskGate
from quanterback.domain.backtest import BacktestReport
from quanterback.domain.risk import RiskThresholds


def _report(**kw) -> BacktestReport:
    base = dict(
        ticker="X", strategy="MOMENTUM", params={},
        period_start=date(2023,1,1), period_end=date(2026,1,1),
        num_trades=30, win_rate=0.5, max_drawdown=0.15, sharpe=1.0,
        profit_factor=1.5, cumulative_return=0.30, avg_trade_return=0.01,
        avg_bars_held=10.0, trades=[],
        buy_and_hold_return=0.20, buy_and_hold_max_drawdown=0.25,
        excess_return=0.10, drawdown_ratio=0.60,
        oos_num_trades=10, oos_win_rate=0.55, oos_max_drawdown=0.12,
        oos_sharpe=1.2, oos_cumulative_return=0.15, oos_excess_return=0.05,
    )
    base.update(kw)
    return BacktestReport(**base)


def test_sanity_reject_too_few_trades() -> None:
    a = CompositeRiskGate().evaluate(_report(num_trades=3), RiskThresholds())
    assert not a.passed
    assert "sanity_min_num_trades" in a.failed_checks
    assert a.size_multiplier == 0.0


def test_sanity_reject_catastrophic_max_dd() -> None:
    a = CompositeRiskGate().evaluate(_report(max_drawdown=0.65), RiskThresholds())
    assert not a.passed
    assert "sanity_max_drawdown" in a.failed_checks


def test_sanity_reject_no_oos_evidence() -> None:
    a = CompositeRiskGate().evaluate(_report(oos_num_trades=0), RiskThresholds())
    assert not a.passed
    assert "sanity_min_oos_trades" in a.failed_checks


def test_relative_reject_strategy_dd_worse_than_bh() -> None:
    a = CompositeRiskGate().evaluate(_report(drawdown_ratio=1.5), RiskThresholds())
    assert not a.passed
    assert "strategy_dd_worse_than_bh" in a.failed_checks


def test_relative_reject_strategy_lost_and_worse_than_bh() -> None:
    a = CompositeRiskGate().evaluate(
        _report(excess_return=-0.05, cumulative_return=-0.10), RiskThresholds(),
    )
    assert not a.passed
    assert "strategy_worse_than_bh_and_negative" in a.failed_checks


def test_pass_with_high_size_multiplier_for_strong_strategy() -> None:
    a = CompositeRiskGate().evaluate(
        _report(excess_return=0.25, drawdown_ratio=0.4, oos_sharpe=1.8),
        RiskThresholds(),
    )
    assert a.passed
    assert a.size_multiplier > 0.8


def test_pass_with_mid_size_multiplier_for_mediocre_strategy() -> None:
    a = CompositeRiskGate().evaluate(
        _report(excess_return=0.02, drawdown_ratio=0.95, oos_sharpe=0.3),
        RiskThresholds(),
    )
    assert a.passed
    assert 0.25 <= a.size_multiplier <= 0.7


def test_size_multiplier_floor_is_025() -> None:
    a = CompositeRiskGate().evaluate(
        _report(excess_return=-0.05, drawdown_ratio=1.0, oos_sharpe=-0.5),
        RiskThresholds(),
    )
    if a.passed:
        assert a.size_multiplier >= 0.25
