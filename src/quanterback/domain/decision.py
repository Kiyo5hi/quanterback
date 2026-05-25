from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from quanterback.domain.agents import AgentDebate


class MomentumParams(BaseModel):
    """Parameters for the Momentum strategy."""

    model_config = ConfigDict(frozen=True)

    lookback_days: int = Field(
        ge=5, le=60, description="Days to evaluate momentum"
    )
    momentum_threshold: float = Field(
        ge=0.0, le=0.30,
        description="Required cumulative return over lookback window",
    )


class MeanReversionParams(BaseModel):
    """Parameters for the Mean Reversion strategy."""

    model_config = ConfigDict(frozen=True)

    lookback_days: int = Field(
        ge=5, le=60, description="Days for rolling mean and stddev"
    )
    entry_z_score: float = Field(
        ge=1.0, le=4.0,
        description="Std deviations below mean to trigger entry. 2.0 is typical.",
    )


class StrategyDecision(BaseModel):
    """LLM output. JSON-schema-enforced. See spec §4.2."""

    model_config = ConfigDict(frozen=True)

    action: Literal["BUY", "PASS"]
    ticker: str
    strategy: Literal["MOMENTUM", "MEAN_REVERSION"]
    params: MomentumParams | MeanReversionParams | None = None
    # Generous upper bound — Chinese rationales often run 1000+ chars; we want
    # to keep them rather than reject the whole decision. Lower bound stays
    # tight so we still catch empty / one-liner explanations.
    rationale: str = Field(min_length=20, max_length=2000)
    confidence: float = Field(ge=0.0, le=1.0)
    news_sentiment: float = Field(ge=-1.0, le=1.0, default=0.0)
    agent_debate: AgentDebate | None = None

    @model_validator(mode="after")
    def _params_required_for_buy(self) -> StrategyDecision:
        if self.action == "BUY" and self.params is None:
            raise ValueError("BUY action requires non-null params")
        return self
