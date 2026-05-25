from __future__ import annotations

from quanterback.domain.risk import RiskAssessment, RiskThresholds


def test_risk_thresholds_have_defaults() -> None:
    t = RiskThresholds()
    assert t.max_drawdown == 0.50
    assert t.min_sharpe == -0.5
    assert t.min_num_trades == 5


def test_risk_thresholds_max_drawdown_default_is_50_pct() -> None:
    t = RiskThresholds()
    # Sanity cap for single-stock momentum
    assert t.max_drawdown == 0.50


def test_risk_assessment_with_failures() -> None:
    a = RiskAssessment(passed=False, failed_checks=["max_drawdown", "min_sharpe"])
    assert not a.passed
    assert len(a.failed_checks) == 2
