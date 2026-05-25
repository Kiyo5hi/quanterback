# ruff: noqa: I001
from __future__ import annotations

import logging

from openai import BadRequestError, OpenAI
from openai.types.chat import ChatCompletion

from quanterback.interfaces.decision import ChatMessage, ChatResponse

log = logging.getLogger(__name__)

ARK_DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
TOOL_NAME = "submit_strategy_decision"


class ArkClient:
    """LLMClient adapter over Volcengine Ark using OpenAI-compatible API.

    Structured-output strategy (preferred → fallback):
      1. tool_calls (server enforces JSON schema)
      2. response_format=json_object (looser, may invent enums)
      3. plain prompt (relies on prompt + client-side validation)

    Each tier is attempted on first use; failures (400 BadRequest mentioning
    the unsupported feature) are remembered for the wrapper lifetime.
    """

    def __init__(
        self, *, api_key: str, model: str,
        base_url: str = ARK_DEFAULT_BASE_URL,
        max_tokens: int = 1024,
        thinking_effort: str = "off",
    ) -> None:
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._max_tokens = max_tokens
        self._thinking_effort = thinking_effort
        self._use_tools: bool | None = None
        self._use_response_format: bool | None = None

    def _build_extra_body(self) -> dict | None:
        if self._thinking_effort == "off":
            return None
        return {"thinking": {"type": "enabled"}}

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        response_schema: dict | None = None,
        temperature: float = 0.0,
    ) -> ChatResponse:
        oai_messages = [{"role": m.role, "content": m.content} for m in messages]
        base_kwargs: dict = {
            "model": self._model,
            "messages": oai_messages,
            "temperature": temperature,
            "max_tokens": self._max_tokens,
        }
        extra_body = self._build_extra_body()
        if extra_body is not None:
            base_kwargs["extra_body"] = extra_body

        if response_schema is None:
            resp = self._client.chat.completions.create(**base_kwargs)
            return self._to_response(resp, resp.choices[0].message.content or "")

        # Tier 1: tool/function call
        if self._use_tools is not False:
            try:
                resp = self._client.chat.completions.create(
                    **base_kwargs,
                    tools=[{
                        "type": "function",
                        "function": {
                            "name": TOOL_NAME,
                            "description": (
                                "Submit a single trading decision matching the schema. "
                                "Call this tool exactly once."
                            ),
                            "parameters": response_schema,
                        },
                    }],
                    tool_choice={
                        "type": "function",
                        "function": {"name": TOOL_NAME},
                    },
                )
                self._use_tools = True
                msg = resp.choices[0].message
                tool_calls = getattr(msg, "tool_calls", None)
                # Some models force-respond as text even with tool_choice;
                # take their content as last resort
                # noqa: SIM108 - multiline ternary is more readable here
                content = (
                    tool_calls[0].function.arguments
                    if tool_calls
                    else msg.content or ""
                )
                return self._to_response(resp, content)
            except BadRequestError as e:
                err_text = str(e).lower()
                if "tool" in err_text or "function" in err_text:
                    log.warning(
                        "Ark model %s does not support tool calls; "
                        "falling back to response_format.",
                        self._model,
                    )
                    self._use_tools = False
                else:
                    raise

        # Tier 2: response_format=json_object
        if self._use_response_format is not False:
            try:
                resp = self._client.chat.completions.create(
                    **base_kwargs,
                    response_format={"type": "json_object"},
                )
                self._use_response_format = True
                return self._to_response(resp, resp.choices[0].message.content or "")
            except BadRequestError as e:
                if "response_format" in str(e):
                    log.warning(
                        "Ark model %s does not support response_format; "
                        "falling back to plain prompt.",
                        self._model,
                    )
                    self._use_response_format = False
                else:
                    raise

        # Tier 3: plain
        resp = self._client.chat.completions.create(**base_kwargs)
        return self._to_response(resp, resp.choices[0].message.content or "")

    def _to_response(self, resp: ChatCompletion, content: str) -> ChatResponse:
        usage = resp.usage
        return ChatResponse(
            content=content,
            model=getattr(resp, "model", self._model),
            usage={
                "input_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
                "output_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
            },
        )
