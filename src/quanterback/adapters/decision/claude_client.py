from __future__ import annotations

import logging

from anthropic import Anthropic

from quanterback.interfaces.decision import ChatMessage, ChatResponse

log = logging.getLogger(__name__)

_CLAUDE_THINKING_BUDGETS = {
    "low": 1024,
    "medium": 4096,
    "high": 16384,
}  # "off" not present → no thinking param


class ClaudeClient:
    """LLMClient adapter over the Anthropic Python SDK."""

    def __init__(
        self, *, api_key: str, model: str, max_tokens: int = 1024,
        thinking_effort: str = "off",
    ) -> None:
        self._client = Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._thinking_effort = thinking_effort

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        response_schema: dict | None = None,
        temperature: float = 0.0,
    ) -> ChatResponse:
        system_parts = [m.content for m in messages if m.role == "system"]
        chat_messages = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role != "system"
        ]
        kwargs: dict = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": temperature,
            "system": "\n\n".join(system_parts) if system_parts else "",
            "messages": chat_messages,
        }
        budget = _CLAUDE_THINKING_BUDGETS.get(self._thinking_effort)
        if budget is not None:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
            # Claude extended thinking requires temperature=1.0
            if temperature != 1.0:
                log.warning(
                    "Claude extended thinking requires temperature=1.0; overriding "
                    "requested %s.", temperature,
                )
                kwargs["temperature"] = 1.0
        if response_schema is not None:
            kwargs["extra_body"] = {"response_format": {
                "type": "json_schema",
                "schema": response_schema,
            }}
        resp = self._client.messages.create(**kwargs)
        text = "".join(getattr(block, "text", "") for block in resp.content)
        return ChatResponse(
            content=text,
            model=getattr(resp, "model", self._model),
            usage={
                "input_tokens": getattr(resp.usage, "input_tokens", 0),
                "output_tokens": getattr(resp.usage, "output_tokens", 0),
            },
        )
