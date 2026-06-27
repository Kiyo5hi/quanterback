from __future__ import annotations

from typing import Any, Literal, Protocol

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


class ChatTool(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    description: str
    input_schema: dict[str, Any]


class ChatToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    arguments: dict[str, Any]
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


class ToolCallingLLMClient(LLMClient, Protocol):
    def chat_tool_call(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ChatTool],
        temperature: float = 0.0,
    ) -> ChatToolCall | None: ...


class LLMStrategist(Protocol):
    def decide(self, summary: CondensedSummary) -> StrategyDecision: ...


class ApprovalResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    approved: bool
    reason: str
    approver: str | None = None


class ApprovalGate(Protocol):
    def review(self, decision: StrategyDecision) -> ApprovalResult: ...
