from __future__ import annotations

from dataclasses import dataclass

from quanterback.interfaces.decision import ChatMessage, ChatResponse


@dataclass
class FakeLLMClient:
    """Returns canned JSON responses. Records the last input."""
    canned_content: str
    last_messages: list[ChatMessage] | None = None
    last_schema: dict | None = None

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        response_schema: dict | None = None,
        temperature: float = 0.0,
    ) -> ChatResponse:
        self.last_messages = messages
        self.last_schema = response_schema
        return ChatResponse(
            content=self.canned_content,
            model="fake",
            usage={"input_tokens": 0, "output_tokens": 0},
        )
