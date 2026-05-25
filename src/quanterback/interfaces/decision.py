from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from quanterback.domain.decision import StrategyDecision
from quanterback.domain.market import CondensedSummary


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)
    role: Literal["system", "user", "assistant"]
    content: str


class ChatResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    content: str
    model: str
    usage: dict


class LLMClient(Protocol):
    def chat(
        self,
        messages: list[ChatMessage],
        *,
        response_schema: dict | None = None,
        temperature: float = 0.0,
    ) -> ChatResponse: ...


class LLMStrategist(Protocol):
    def decide(self, summary: CondensedSummary) -> StrategyDecision: ...


class ApprovalResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    approved: bool
    reason: str
    approver: str | None = None


class ApprovalGate(Protocol):
    def review(self, decision: StrategyDecision) -> ApprovalResult: ...
