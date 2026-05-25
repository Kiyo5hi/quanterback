from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RiskThresholds(BaseModel):
    """Sanity-cap thresholds. Real evaluation is relative + OOS in CompositeRiskGate."""

    model_config = ConfigDict(frozen=True)

    # 0.50: sanity cap, not rejection threshold. Real gate uses relative metrics.
    max_drawdown: float = Field(default=0.50, ge=0, le=1)
    min_sharpe: float = Field(default=-0.5)
    min_win_rate: float = Field(default=0.0, ge=0, le=1)
    min_profit_factor: float = Field(default=0.0, ge=0)
    # 5: sanity cap for single-stock momentum.
    min_num_trades: int = Field(default=5, ge=0)


class RiskAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)
    passed: bool
    failed_checks: list[str]
    size_multiplier: float = 1.0
