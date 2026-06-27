from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, cast

from pydantic import ValidationError

from quanterback.chat.models import ChatIntent
from quanterback.interfaces.decision import (
    ChatMessage,
    ChatTool,
    LLMClient,
    ToolCallingLLMClient,
)
from quanterback.tools.registry import ToolManifest

log = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

_INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["tool", "help", "unknown"]},
        "tool_name": {"type": ["string", "null"]},
        "params": {"type": "object"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": ["kind", "tool_name", "params", "confidence"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class LLMIntentResolver:
    llm_client: LLMClient

    def resolve(self, text: str, tools: list[ToolManifest]) -> ChatIntent:
        if not tools:
            return ChatIntent(kind="unknown", confidence=0.0)

        tool_names = {tool.name for tool in tools}
        tool_intent = self._resolve_with_tool_call(text, tools)
        if tool_intent is not None:
            if tool_intent.tool_name in tool_names:
                return tool_intent
            log.warning("LLM selected unavailable tool: %s", tool_intent.tool_name)
            return ChatIntent(kind="unknown", confidence=0.0)

        return self._resolve_with_json_intent(text, tools, tool_names)

    def _resolve_with_tool_call(
        self, text: str, tools: list[ToolManifest],
    ) -> ChatIntent | None:
        if not hasattr(self.llm_client, "chat_tool_call"):
            return None
        names = _tool_name_map(tools)
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You are an intent router for a Telegram finance assistant. "
                    "Call exactly one available function when the user's request "
                    "matches a supported tool. Do not call a function for unsupported "
                    "requests. Normalize ticker symbols to uppercase."
                ),
            ),
            ChatMessage(
                role="user",
                content=text,
            ),
        ]
        chat_tools = [
            ChatTool(
                name=safe_name,
                description=tool.description,
                input_schema=tool.input_schema,
            )
            for safe_name, tool in names.items()
        ]
        client = cast(ToolCallingLLMClient, self.llm_client)
        try:
            call = client.chat_tool_call(
                messages,
                tools=chat_tools,
                temperature=0.0,
            )
        except Exception as exc:
            log.warning("LLM function-call intent routing failed: %s", exc)
            return None
        if call is None:
            return None
        tool = names.get(call.name)
        if tool is None:
            log.warning("LLM called unknown function: %s", call.name)
            return ChatIntent(kind="unknown", confidence=0.0)
        return ChatIntent(
            kind="tool",
            tool_name=tool.name,
            params=call.arguments,
            confidence=0.9,
        )

    def _resolve_with_json_intent(
        self, text: str, tools: list[ToolManifest], tool_names: set[str],
    ) -> ChatIntent:
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You are an intent router for a Telegram finance assistant. "
                    "Return one JSON object only. Choose at most one tool from "
                    "the provided tool list. Do not invent tools. If the user "
                    "asks for something outside the available tools, return "
                    'kind=\"unknown\". For ticker symbols, normalize to uppercase. '
                    "For tools whose input schema uses tickers, provide a list only "
                    "when the schema asks for an array; otherwise provide ticker."
                ),
            ),
            ChatMessage(
                role="user",
                content=json.dumps(
                    {
                        "available_tools": [_tool_payload(tool) for tool in tools],
                        "user_message": text,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        ]
        try:
            response = self.llm_client.chat(
                messages, response_schema=_INTENT_SCHEMA, temperature=0.0,
            )
            payload = _parse_json_object(response.content)
            intent = ChatIntent.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            log.warning("LLM intent routing failed: %s", exc)
            return ChatIntent(kind="unknown", confidence=0.0)

        if intent.kind == "tool" and intent.tool_name not in tool_names:
            log.warning("LLM selected unavailable tool: %s", intent.tool_name)
            return ChatIntent(kind="unknown", confidence=0.0)
        if intent.kind != "tool":
            return ChatIntent(kind=intent.kind, confidence=intent.confidence)
        return intent


def _tool_payload(tool: ToolManifest) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
        "requires_confirmation": tool.requires_confirmation,
        "side_effect": tool.side_effect.value,
    }


def _tool_name_map(tools: list[ToolManifest]) -> dict[str, ToolManifest]:
    out: dict[str, ToolManifest] = {}
    for tool in tools:
        safe_name = re.sub(r"[^A-Za-z0-9_-]", "__", tool.name)
        if safe_name in out:
            raise ValueError(f"tool name collision after sanitizing: {tool.name}")
        out[safe_name] = tool
    return out


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = _JSON_FENCE_RE.search(stripped)
    if fenced:
        stripped = fenced.group(1).strip()
    return json.loads(stripped)
