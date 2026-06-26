from __future__ import annotations

from typing import Any

from quanterback.capabilities.research import ResearchAnalyzer
from quanterback.tools.registry import (
    Tool,
    ToolContext,
    ToolManifest,
    ToolResult,
    ToolSideEffect,
)


def analyze_ticker_tool(analyzer: ResearchAnalyzer) -> Tool:
    def _handle(params: dict[str, Any], context: ToolContext) -> ToolResult:
        ticker = str(params.get("ticker") or "").strip().upper()
        if not ticker:
            return ToolResult(ok=False, message="ticker is required")
        result = analyzer.analyze_ticker(ticker)
        decision = result.decision
        summary = result.summary
        return ToolResult(
            ok=True,
            message=decision.rationale,
            data={
                "ticker": result.ticker,
                "action": decision.action,
                "strategy": decision.strategy,
                "confidence": decision.confidence,
                "news_sentiment": decision.news_sentiment,
                "rationale": decision.rationale,
                "model": result.model_name,
                "summary": summary.model_dump(mode="json"),
                "decision": decision.model_dump(mode="json"),
            },
        )

    return Tool(
        manifest=ToolManifest(
            name="research.analyze_ticker",
            description=(
                "Analyze one ticker using market data, news, fundamentals, "
                "and the research strategist. This never submits orders."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Ticker symbol to analyze, e.g. NVDA",
                    },
                },
                "required": ["ticker"],
                "additionalProperties": False,
            },
            side_effect=ToolSideEffect.READ_ONLY,
            scope="research",
            allowed_interfaces=("research_chat", "agent", "cli"),
            requires_confirmation=False,
            requires_setup=("llm", "market_data"),
            default_enabled=True,
        ),
        handler=_handle,
    )

