"""Decorator RiskGate: rejects BUY when sector-level $ exposure cap would be exceeded."""
from __future__ import annotations

import logging

from quanterback.adapters.risk.sectors import get_sector
from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.backtest import BacktestReport
from quanterback.domain.risk import RiskAssessment, RiskThresholds
from quanterback.interfaces.execution import Executor
from quanterback.interfaces.risk import RiskGate

log = logging.getLogger(__name__)


class SectorExposureRiskGate:
    """Caps $ exposure per sector. Same accounting as TotalExposureRiskGate
    but filtered by sector lookup."""

    def __init__(
        self, inner: RiskGate, store: SqliteStore, executor: Executor, *,
        max_sector_exposure_pct: float, position_size_pct: float,
    ) -> None:
        self._inner = inner
        self._store = store
        self._executor = executor
        self._max_pct = max_sector_exposure_pct
        self._pos_pct = position_size_pct

    def evaluate(
        self, report: BacktestReport, thresholds: RiskThresholds,
    ) -> RiskAssessment:
        inner_assessment = self._inner.evaluate(report, thresholds)
        if not inner_assessment.passed:
            return inner_assessment

        new_sector = get_sector(report.ticker)
        try:
            account_value = self._executor.get_account_value()
        except Exception:
            log.warning("get_account_value failed in SectorExposureRiskGate; allowing")
            return inner_assessment

        open_positions = self._store.get_open_positions()
        sector_existing = sum(
            (p.qty or 0) * (p.entry_price or 0)
            for p in open_positions
            if get_sector(p.ticker) == new_sector
        )
        new_max = self._pos_pct * account_value
        cap = self._max_pct * account_value

        if sector_existing + new_max > cap:
            log.info(
                "Sector exposure rejected %s: sector=%s existing=$%.0f + new_max=$%.0f > cap=$%.0f",
                report.ticker, new_sector, sector_existing, new_max, cap,
            )
            return RiskAssessment(
                passed=False,
                failed_checks=[*inner_assessment.failed_checks, f"sector_exposure:{new_sector}"],
                size_multiplier=0.0,
            )
        return inner_assessment
