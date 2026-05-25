"""PDT-aware RiskGate decorator. Off by default; enable for live trading
when account_equity < $25,000.

FINRA Pattern Day Trader rule: an account that executes >= 4 day trades
in 5 business days while equity < $25,000 is flagged for 90-day
lockout. This gate rejects new BUY decisions when the account is one
day trade away from that threshold.

See spec §9 Open Question #11.
"""
from __future__ import annotations

import logging

from quanterback.domain.backtest import BacktestReport
from quanterback.domain.risk import RiskAssessment, RiskThresholds
from quanterback.interfaces.execution import Executor
from quanterback.interfaces.risk import RiskGate

log = logging.getLogger(__name__)


class PdtAwareRiskGate:
    """Wraps an inner RiskGate; vetoes BUYs that would push the account
    into PDT territory."""

    def __init__(
        self,
        inner: RiskGate,
        executor: Executor,
        *,
        min_equity: float = 25_000.0,
        max_day_trades: int = 3,
    ) -> None:
        self._inner = inner
        self._executor = executor
        self._min_equity = min_equity
        self._max_day_trades = max_day_trades

    def evaluate(
        self, report: BacktestReport, thresholds: RiskThresholds,
    ) -> RiskAssessment:
        inner_assessment = self._inner.evaluate(report, thresholds)
        if not inner_assessment.passed:
            return inner_assessment

        try:
            equity = self._executor.get_account_value()
            day_trades = self._executor.get_day_trade_count()
        except Exception as e:
            log.warning("PdtAwareRiskGate could not fetch account state: %s", e)
            # Conservative: if we can't tell, don't block. Inner already passed.
            return inner_assessment

        if equity < self._min_equity and day_trades >= self._max_day_trades:
            log.info(
                "PDT protection triggered: equity=$%.2f < $%.0f, "
                "day_trades=%d >= %d.",
                equity, self._min_equity, day_trades, self._max_day_trades,
            )
            return RiskAssessment(
                passed=False,
                failed_checks=[*inner_assessment.failed_checks, "pdt_protection"],
            )

        return inner_assessment
