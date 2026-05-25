from __future__ import annotations

from typing import Protocol

from quanterback.domain.backtest import BacktestReport, BacktestRequest
from quanterback.domain.decision import StrategyDecision
from quanterback.domain.market import CondensedSummary
from quanterback.domain.order import BracketOrderSpec
from quanterback.domain.position import OpenLifecycle
from quanterback.domain.risk import RiskAssessment, RiskThresholds


class PositionStateService(Protocol):
    def has_open_lifecycle(self, ticker: str) -> bool: ...
    def get_open(self, ticker: str) -> OpenLifecycle | None: ...


class Backtester(Protocol):
    def run(self, request: BacktestRequest) -> BacktestReport: ...


class RiskGate(Protocol):
    def evaluate(
        self, report: BacktestReport, thresholds: RiskThresholds
    ) -> RiskAssessment: ...


class OrderBuilder(Protocol):
    def build(
        self,
        decision: StrategyDecision,
        summary: CondensedSummary,
        account_value: float,
        *,
        size_multiplier: float = 1.0,
    ) -> BracketOrderSpec: ...
