"""Multi-agent strategist orchestrator following TradingAgents pattern."""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from quanterback.domain.agents import AgentDebate, Thesis
from quanterback.domain.decision import StrategyDecision
from quanterback.domain.market import CondensedSummary
from quanterback.interfaces.decision import ChatMessage, LLMClient

log = logging.getLogger(__name__)


def _strip_markdown_fences(text: str) -> str:
    """Some chat models wrap JSON in ```json fences even when told not to.

    Strip them so json.loads can succeed.
    """
    t = text.strip()
    if t.startswith("```"):
        # remove opening fence (with optional `json` language tag) and closing fence
        first_nl = t.find("\n")
        if first_nl != -1:
            t = t[first_nl + 1:]
        if t.endswith("```"):
            t = t[:-3].rstrip()
    return t


class MultiAgentStrategist:
    """Orchestrates 4 specialized agents (Fundamentalist, Technician, Sentiment, Risk Manager).

    Each analyst gets data slice + focused prompt. Risk Manager sees the 3 theses
    + base summary and produces the final Decision.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        prompts_dir: Path,
        *,
        language: str = "en",
        parallel: bool = True,
    ) -> None:
        self._llm_client = llm_client
        self._prompts_dir = Path(prompts_dir)
        self._language = language
        self._parallel = parallel
        self._market_context: dict[str, str] = {}
        self._current_positions: list[dict] | None = None

    def set_market_context(self, ctx: dict[str, str]) -> None:
        self._market_context = ctx or {}

    def set_current_positions(self, positions: list[dict] | None) -> None:
        """Set current open positions for risk manager context.

        Each position should be a dict with keys:
        - ticker: str
        - qty: float
        - entry_price: float
        - sl: float
        - tp: float
        - days_held: float (or hours, converted to days)
        """
        self._current_positions = positions

    def decide(self, summary: CondensedSummary) -> StrategyDecision:
        """Run all analyst agents in parallel (or sequentially), then risk manager."""
        if self._parallel:
            with ThreadPoolExecutor(max_workers=3) as pool:
                fa_fut = pool.submit(self._run_fundamentalist, summary)
                ta_fut = pool.submit(self._run_technician, summary)
                sa_fut = pool.submit(self._run_sentiment, summary)

                fa = fa_fut.result()
                ta = ta_fut.result()
                sa = sa_fut.result()
        else:
            fa = self._run_fundamentalist(summary)
            ta = self._run_technician(summary)
            sa = self._run_sentiment(summary)

        debate = AgentDebate(fundamentalist=fa, technician=ta, sentiment=sa)
        for thesis in (fa, ta, sa):
            if thesis is not None:
                log.info(
                    "[%s/%s] %s conf=%.2f — %s | points: %s",
                    summary.ticker, thesis.agent, thesis.lean,
                    thesis.confidence, thesis.rationale,
                    " · ".join(thesis.key_points),
                )
        decision = self._run_risk_manager(summary, debate)
        log.info(
            "[%s/risk_manager] %s strategy=%s conf=%.2f — %s",
            summary.ticker, decision.action, decision.strategy,
            decision.confidence, decision.rationale,
        )
        # Attach the debate to the decision for persistence
        decision = decision.model_copy(update={"agent_debate": debate})
        return decision

    # ===== Analyst agents =====

    def _run_fundamentalist(self, summary: CondensedSummary) -> Thesis | None:
        try:
            prompt_path = self._prompts_dir / f"agent_fundamentalist_{self._language}.md"
            slice_data = self._slice_for_fundamentalist(summary)
            return self._call_agent(prompt_path, slice_data, "fundamentalist")
        except Exception as e:
            log.exception("Fundamentalist agent failed: %s", e)
            return None

    def _run_technician(self, summary: CondensedSummary) -> Thesis | None:
        try:
            prompt_path = self._prompts_dir / f"agent_technician_{self._language}.md"
            slice_data = self._slice_for_technician(summary)
            return self._call_agent(prompt_path, slice_data, "technician")
        except Exception as e:
            log.exception("Technician agent failed: %s", e)
            return None

    def _run_sentiment(self, summary: CondensedSummary) -> Thesis | None:
        try:
            prompt_path = self._prompts_dir / f"agent_sentiment_{self._language}.md"
            slice_data = self._slice_for_sentiment(summary)
            return self._call_agent(prompt_path, slice_data, "sentiment")
        except Exception as e:
            log.exception("Sentiment agent failed: %s", e)
            return None

    def _run_risk_manager(
        self, summary: CondensedSummary, debate: AgentDebate
    ) -> StrategyDecision:
        try:
            prompt_path = self._prompts_dir / f"agent_risk_manager_{self._language}.md"
            return self._call_risk_manager(prompt_path, summary, debate)
        except Exception as e:
            log.exception("Risk manager failed: %s", e)
            # Fail-safe: PASS with low confidence
            return StrategyDecision(
                action="PASS",
                ticker=summary.ticker,
                strategy="MOMENTUM",
                rationale=f"Risk manager error: {str(e)[:100]}",
                confidence=0.0,
            )

    # ===== Data slicing =====

    @staticmethod
    def _slice_for_fundamentalist(summary: CondensedSummary) -> dict:
        return {
            "ticker": summary.ticker,
            "fundamentals": summary.fundamentals.model_dump(),
            "insider_activity": (
                summary.insider_activity.model_dump()
                if summary.insider_activity
                else None
            ),
            "recent_analyst_actions": [
                a.model_dump() for a in (summary.recent_analyst_actions or [])
            ],
            "short_interest": (
                summary.short_interest.model_dump() if summary.short_interest else None
            ),
            "eps_trend": (
                summary.eps_trend.model_dump() if summary.eps_trend else None
            ),
        }

    @staticmethod
    def _slice_for_technician(summary: CondensedSummary) -> dict:
        return {
            "ticker": summary.ticker,
            "price": summary.price.model_dump(),
            "moving_averages": summary.moving_averages.model_dump(),
            "volatility": summary.volatility.model_dump(),
            "volume": summary.volume.model_dump(),
            "technicals": summary.technicals.model_dump(),
            "trend_regime": summary.trend_regime.value,
            "momentum_signals": (
                summary.momentum_signals.model_dump() if summary.momentum_signals else None
            ),
            "intraday": (
                summary.intraday.model_dump() if summary.intraday else None
            ),
        }

    @staticmethod
    def _slice_for_sentiment(summary: CondensedSummary) -> dict:
        return {
            "ticker": summary.ticker,
            "news": [n.model_dump() for n in (summary.news or [])],
        }

    # ===== LLM calls =====

    def _call_agent(
        self, prompt_path: Path, data: dict, agent_name: str
    ) -> Thesis | None:
        """Make an LLM call for an analyst agent returning a Thesis."""
        system_prompt = prompt_path.read_text()
        user_msg = (
            f"Analyze this ticker. Respond with ONLY a JSON object. Data:\n"
            f"```json\n{json.dumps(data, indent=2, default=str)}\n```"
        )

        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_msg),
        ]

        try:
            resp = self._llm_client.chat(messages, temperature=0.0)
            content = _strip_markdown_fences(resp.content)
            data_out = json.loads(content)
            data_out["agent"] = agent_name  # Ensure agent name is set
            return Thesis.model_validate(data_out)
        except Exception as e:
            preview = resp.content[:200] if "resp" in locals() else ""
            log.warning("Agent %s parse failed: %s. Raw: %s", agent_name, e, preview)
            return None

    def _call_risk_manager(
        self, prompt_path: Path, summary: CondensedSummary, debate: AgentDebate
    ) -> StrategyDecision:
        """Make an LLM call for the risk manager producing a Decision."""
        system_prompt = prompt_path.read_text()

        # Build the augmented context
        agent_block = {
            "fundamentalist": (
                debate.fundamentalist.model_dump() if debate.fundamentalist else None
            ),
            "technician": debate.technician.model_dump() if debate.technician else None,
            "sentiment": debate.sentiment.model_dump() if debate.sentiment else None,
        }

        market = self._market_context or {}
        market_line = (
            "Market context:\n" + "\n".join(f"  {k}: {v}" for k, v in market.items())
            if market
            else "Market context: unknown"
        )

        # Build current positions block if available
        positions_block = ""
        if self._current_positions:
            positions_block = (
                "\n\nCurrent open positions:\n```json\n"
                f"{json.dumps(self._current_positions, indent=2, default=str)}\n```"
            )

        user_msg = (
            f"{market_line}\n\n"
            f"Analyst debate (3 specialized agents):\n```json\n"
            f"{json.dumps(agent_block, indent=2, default=str)}\n```\n\n"
            f"Base CondensedSummary:\n{summary.to_prompt_text()}"
            f"{positions_block}"
        )

        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_msg),
        ]

        try:
            resp = self._llm_client.chat(messages, temperature=0.0)
            content = _strip_markdown_fences(resp.content)
            data_out = json.loads(content)
            # Coerce missing fields the LLM may omit (smaller models)
            if not data_out.get("ticker"):
                data_out["ticker"] = summary.ticker
            if not data_out.get("strategy"):
                data_out["strategy"] = "MOMENTUM"
            if data_out.get("action") == "PASS":
                data_out["params"] = None
            return StrategyDecision.model_validate(data_out)
        except Exception as e:
            preview = resp.content[:200] if "resp" in locals() else ""
            log.warning("Risk manager parse failed: %s. Raw: %s", e, preview)
            # Fail-safe
            return StrategyDecision(
                action="PASS",
                ticker=summary.ticker,
                strategy="MOMENTUM",
                rationale=f"Risk manager parse error: {str(e)[:100]}",
                confidence=0.0,
            )
