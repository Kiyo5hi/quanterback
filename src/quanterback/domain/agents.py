"""Multi-agent strategist domain models."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AgentName = Literal["fundamentalist", "technician", "sentiment", "risk_manager"]
Lean = Literal["bullish", "bearish", "neutral"]


class Thesis(BaseModel):
    """One analyst agent's reasoned view."""

    agent: AgentName
    lean: Lean
    confidence: float = Field(ge=0.0, le=1.0)
    key_points: list[str] = Field(default_factory=list, max_length=5)
    rationale: str = Field(min_length=10, max_length=600)


class AgentDebate(BaseModel):
    """Aggregate of all analyst theses before final decision."""

    fundamentalist: Thesis | None = None
    technician: Thesis | None = None
    sentiment: Thesis | None = None
    cost_usd_estimate: float = 0.0
