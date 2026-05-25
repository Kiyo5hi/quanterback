"""Decorator RiskGate: rejects BUY when sector concurrency cap is hit.

Hard cap on open positions per sector (default 2). Prevents the system
from opening AMD when NVDA+ARM are already open — three AI-semi
positions is just one trade with 3x size, not diversification.
"""
from __future__ import annotations

import logging
from collections import Counter

from quanterback.adapters.risk.sectors import get_sector
from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.backtest import BacktestReport
from quanterback.domain.risk import RiskAssessment, RiskThresholds
from quanterback.interfaces.risk import RiskGate

log = logging.getLogger(__name__)


class SectorConcurrencyRiskGate:
    """Wraps an inner RiskGate; vetoes BUYs that would over-concentrate
    open positions in a single sector."""

    def __init__(
        self, inner: RiskGate, store: SqliteStore, *,
        max_per_sector: int = 2,
    ) -> None:
        self._inner = inner
        self._store = store
        self._max = max_per_sector

    def evaluate(
        self, report: BacktestReport, thresholds: RiskThresholds,
    ) -> RiskAssessment:
        inner_assessment = self._inner.evaluate(report, thresholds)
        if not inner_assessment.passed:
            return inner_assessment

        new_sector = get_sector(report.ticker)
        open_lcs = self._store.query_open_lifecycles()
        counts: Counter[str] = Counter()
        for lc in open_lcs:
            counts[get_sector(lc.ticker)] += 1

        if counts[new_sector] >= self._max:
            log.info(
                "Sector concurrency rejected %s: sector=%s already has %d open "
                "(cap=%d)",
                report.ticker, new_sector, counts[new_sector], self._max,
            )
            return RiskAssessment(
                passed=False,
                failed_checks=[*inner_assessment.failed_checks,
                                f"sector_concurrency:{new_sector}"],
                size_multiplier=0.0,
            )

        return inner_assessment
