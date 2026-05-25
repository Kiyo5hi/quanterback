from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from quanterback.adapters.decision.prompt import DECISION_RESPONSE_SCHEMA, render_prompt
from quanterback.domain.decision import StrategyDecision
from quanterback.domain.market import CondensedSummary
from quanterback.interfaces.decision import ChatMessage, LLMClient


class PromptedLLMStrategist:
    """LLMStrategist that uses an LLMClient with a markdown prompt template."""

    def __init__(self, client: LLMClient, prompt_template_path: Path,
                 *, temperature: float = 0.0) -> None:
        self._client = client
        self._template_path = prompt_template_path
        self._temperature = temperature
        self._market_context: dict[str, str] = {}

    def set_market_context(self, ctx: dict[str, str]) -> None:
        self._market_context = ctx or {}

    def decide(self, summary: CondensedSummary) -> StrategyDecision:
        summary_text = summary.to_prompt_text()
        system_text = render_prompt(self._template_path, "")
        ctx_lines = ""
        if self._market_context:
            ctx_lines = "Market context:\n" + "\n".join(
                f"  {k}: {v}" for k, v in self._market_context.items()
            ) + "\n\n"
        user_text = (
            "Here is the CondensedSummary for ticker "
            f"`{summary.ticker}`. Respond with ONLY a JSON object matching the "
            "schema (action, ticker, strategy, params, rationale, confidence). "
            "Do not include any text outside the JSON.\n\n"
            f"{ctx_lines}{summary_text}"
        )

        messages = [
            ChatMessage(role="system", content=system_text),
            ChatMessage(role="user", content=user_text),
        ]
        resp = self._client.chat(
            messages,
            response_schema=DECISION_RESPONSE_SCHEMA,
            temperature=self._temperature,
        )
        try:
            content = _strip_markdown_fences(resp.content)
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM output is not valid JSON: {e}") from e

        # Smaller models (doubao-seed-2-0-lite et al.) frequently emit None
        # for fields they consider redundant when action=PASS — or send
        # `params: {}` even though our schema expects null or a typed shape.
        # Coerce these recoverable shapes; let Pydantic reject the rest.
        if isinstance(data, dict):
            if not data.get("ticker"):
                data["ticker"] = summary.ticker
            if not data.get("strategy"):
                data["strategy"] = "MOMENTUM"
            if data.get("action") == "PASS":
                # PASS implies no parameters; coerce {} or any value to None.
                data["params"] = None
            elif data.get("params") == {}:
                # BUY with empty params is invalid — fall through so Pydantic
                # raises a clear error
                pass

        try:
            return StrategyDecision.model_validate(data)
        except ValidationError as e:
            raise ValueError(f"LLM output failed schema validation: {e}") from e


def _strip_markdown_fences(text: str) -> str:
    """Some chat models (especially Chinese ones) wrap JSON in ```json fences
    even when told not to. Strip them so json.loads can succeed.
    """
    t = text.strip()
    if t.startswith("```"):
        # remove opening fence (with optional `json` language tag) and closing fence
        first_nl = t.find("\n")
        if first_nl != -1:
            t = t[first_nl + 1 :]
        if t.endswith("```"):
            t = t[: -3].rstrip()
    return t
