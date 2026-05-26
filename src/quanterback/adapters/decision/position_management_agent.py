"""Position management agent — re-evaluate held positions for HOLD/TIGHTEN_SL/EXIT."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from pydantic import BaseModel

from quanterback.adapters.decision.multi_agent_strategist import _strip_markdown_fences
from quanterback.domain.market import CondensedSummary
from quanterback.interfaces.decision import ChatMessage, LLMClient

log = logging.getLogger(__name__)


class PositionManagementDecision(BaseModel):
    """Output of position management agent analysis."""
    action: str  # "HOLD" | "TIGHTEN_SL" | "TRIM_HALF" | "EXIT_NOW"
    ticker: str
    new_sl_price: float | None = None
    new_qty_pct: float | None = None  # for TRIM_HALF: fraction to KEEP (0.5 = sell half)
    reasoning: str
    confidence: float


class PositionManagementAgent:
    """Evaluates held positions and decides on HOLD/TIGHTEN_SL/EXIT actions."""

    def __init__(
        self,
        llm_client: LLMClient,
        prompts_dir: Path,
        *,
        language: str = "en",
    ) -> None:
        self._llm_client = llm_client
        self._prompts_dir = Path(prompts_dir)
        self._language = language

    def evaluate(
        self,
        summary: CondensedSummary,
        position_context: dict,
    ) -> PositionManagementDecision:
        """Evaluate a held position and return management action.

        Args:
            summary: CondensedSummary for the ticker (price now, technicals, news, etc.)
            position_context: dict with entry_price, current_price, unrealized_pnl_pct,
                            days_held, current_sl, current_tp, etc.

        Returns:
            PositionManagementDecision with action (HOLD/TIGHTEN_SL/EXIT_NOW)
        """
        try:
            prompt_path = self._prompts_dir / f"position_management_{self._language}.md"
            return self._call_agent(prompt_path, summary, position_context)
        except Exception as e:
            log.exception("Position management agent failed for %s: %s", summary.ticker, e)
            # Fail-safe: HOLD
            return PositionManagementDecision(
                action="HOLD",
                ticker=summary.ticker,
                reasoning=f"Position management error: {str(e)[:100]}",
                confidence=0.0,
            )

    def _call_agent(
        self,
        prompt_path: Path,
        summary: CondensedSummary,
        position_context: dict,
    ) -> PositionManagementDecision:
        """Make LLM call for position management."""
        system_prompt = prompt_path.read_text()

        # Build position data block
        position_data = {
            "ticker": summary.ticker,
            "position": position_context,
            "market_data": {
                "price": summary.price.model_dump(),
                "moving_averages": summary.moving_averages.model_dump(),
                "technicals": summary.technicals.model_dump(),
                "trend_regime": summary.trend_regime.value,
                "volatility": summary.volatility.model_dump(),
            },
            "news": [n.model_dump() for n in (summary.news or [])],
        }

        user_msg = (
            f"Evaluate this held position. Respond with ONLY a JSON object. Data:\n"
            f"```json\n{json.dumps(position_data, indent=2, default=str)}\n```"
        )

        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_msg),
        ]

        try:
            resp = self._llm_client.chat(messages, temperature=0.0)
            content = _strip_markdown_fences(resp.content)
            data_out = json.loads(content)
            data_out["ticker"] = summary.ticker  # Ensure ticker is set
            return PositionManagementDecision.model_validate(data_out)
        except Exception as e:
            preview = resp.content[:200] if "resp" in locals() else ""
            log.warning("Position management parse failed for %s: %s. Raw: %s",
                       summary.ticker, e, preview)
            # Fail-safe: HOLD
            return PositionManagementDecision(
                action="HOLD",
                ticker=summary.ticker,
                reasoning=f"Parse error: {str(e)[:100]}",
                confidence=0.0,
            )
