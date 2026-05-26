"""Decorator RiskGate: rejects BUY when total $ exposure cap would be exceeded."""
from __future__ import annotations

import logging

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.backtest import BacktestReport
from quanterback.domain.risk import RiskAssessment, RiskThresholds
from quanterback.interfaces.execution import Executor
from quanterback.interfaces.risk import RiskGate

log = logging.getLogger(__name__)


class TotalExposureRiskGate:
    """Caps sum(qty * entry_price) across all open positions as % of account.

    Estimates new position max size conservatively (position_size_pct *
    account_value, no multiplier discount). If even max would exceed cap → reject.
    """

    def __init__(
        self, inner: RiskGate, store: SqliteStore, executor: Executor, *,
        max_total_exposure_pct: float, position_size_pct: float,
    ) -> None:
        self._inner = inner
        self._store = store
        self._executor = executor
        self._max_pct = max_total_exposure_pct
        self._pos_pct = position_size_pct

    def evaluate(
        self, report: BacktestReport, thresholds: RiskThresholds,
    ) -> RiskAssessment:
        inner_assessment = self._inner.evaluate(report, thresholds)
        if not inner_assessment.passed:
            return inner_assessment

        try:
            account_value = self._executor.get_account_value()
        except Exception:
            log.warning("get_account_value failed in TotalExposureRiskGate; allowing")
            return inner_assessment

        open_positions = self._store.get_open_positions()
        existing = sum((p.qty or 0) * (p.entry_price or 0) for p in open_positions)
        new_max = self._pos_pct * account_value
        cap = self._max_pct * account_value

        if existing + new_max > cap:
            log.info(
                "Total exposure rejected %s: existing=$%.0f + new_max=$%.0f > cap=$%.0f",
                report.ticker, existing, new_max, cap,
            )
            return RiskAssessment(
                passed=False,
                failed_checks=[*inner_assessment.failed_checks, "total_exposure_exceeded"],
                size_multiplier=0.0,
            )
        return inner_assessment
