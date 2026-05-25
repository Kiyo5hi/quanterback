from __future__ import annotations

from quanterback.domain.backtest import BacktestReport
from quanterback.domain.risk import RiskAssessment, RiskThresholds


class ThresholdRiskGate:
    """All checks must pass. Lists every failed check name for transparency."""

    def evaluate(
        self, report: BacktestReport, thresholds: RiskThresholds
    ) -> RiskAssessment:
        failed: list[str] = []
        if report.max_drawdown > thresholds.max_drawdown:
            failed.append("max_drawdown")
        if report.sharpe < thresholds.min_sharpe:
            failed.append("min_sharpe")
        if report.win_rate < thresholds.min_win_rate:
            failed.append("min_win_rate")
        if report.profit_factor < thresholds.min_profit_factor:
            failed.append("min_profit_factor")
        if report.num_trades < thresholds.min_num_trades:
            failed.append("min_num_trades")
        return RiskAssessment(passed=not failed, failed_checks=failed)
