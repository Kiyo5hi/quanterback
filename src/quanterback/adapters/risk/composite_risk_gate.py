from __future__ import annotations

from quanterback.domain.backtest import BacktestReport
from quanterback.domain.risk import RiskAssessment, RiskThresholds


class CompositeRiskGate:
    """Sanity caps + relative gate + sizing. Replaces hard absolute-threshold rejection.

    Three layers:
    1. Sanity caps: reject if backtest is catastrophic (MaxDD > 50%, < 5 trades, etc.)
    2. Relative gate: reject if strategy strictly worse than buy-and-hold
    3. Sizing: compute size_multiplier ∈ [0.25, 1.0] based on excess return + DD ratio + OOS Sharpe
    """

    def evaluate(
        self, report: BacktestReport, thresholds: RiskThresholds,
    ) -> RiskAssessment:
        failed: list[str] = []

        # Sanity caps
        if report.num_trades < thresholds.min_num_trades:
            failed.append("sanity_min_num_trades")
        if report.max_drawdown > thresholds.max_drawdown:
            failed.append("sanity_max_drawdown")
        if report.oos_num_trades < 2:
            failed.append("sanity_min_oos_trades")
        if report.sharpe < thresholds.min_sharpe:
            failed.append("sanity_min_sharpe")

        if failed:
            return RiskAssessment(passed=False, failed_checks=failed,
                                   size_multiplier=0.0)

        # Relative gates
        if report.excess_return < 0 and report.cumulative_return < 0:
            failed.append("strategy_worse_than_bh_and_negative")
        if report.oos_excess_return < 0 and report.oos_cumulative_return < -0.10:
            failed.append("oos_loss_relative_and_absolute")
        if report.drawdown_ratio >= 1.2:
            failed.append("strategy_dd_worse_than_bh")

        if failed:
            return RiskAssessment(passed=False, failed_checks=failed,
                                   size_multiplier=0.0)

        # Sizing
        score = 0.0
        score += min(max(report.excess_return, -0.30), 0.30) * 1.5
        score += (1.0 - min(report.drawdown_ratio, 1.2)) * 0.5
        score += min(max(report.oos_sharpe, -1.0), 2.0) * 0.15
        size_multiplier = max(0.25, min(1.0, 0.5 + score))

        return RiskAssessment(passed=True, failed_checks=[],
                               size_multiplier=size_multiplier)
