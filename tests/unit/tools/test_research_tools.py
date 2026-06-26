from __future__ import annotations

from dataclasses import dataclass

from quanterback.domain.decision import StrategyDecision
from quanterback.tools.registry import ToolContext, ToolRegistry, ToolSideEffect
from quanterback.tools.research import analyze_ticker_tool


@dataclass
class FakeAnalysis:
    ticker: str
    summary: object
    decision: StrategyDecision
    model_name: str = "fake-model"


class FakeSummary:
    def model_dump(self, mode: str = "python"):
        return {"ticker": "NVDA"}


@dataclass
class FakeAnalyzer:
    last_ticker: str | None = None

    def analyze_ticker(self, ticker: str) -> FakeAnalysis:
        self.last_ticker = ticker
        return FakeAnalysis(
            ticker=ticker,
            summary=FakeSummary(),
            decision=StrategyDecision(
                action="PASS",
                ticker=ticker,
                strategy="MOMENTUM",
                params=None,
                rationale="Research-only analysis says the signal is not compelling.",
                confidence=0.5,
            ),
        )


def test_analyze_ticker_tool_manifest_is_research_read_only() -> None:
    tool = analyze_ticker_tool(FakeAnalyzer())  # type: ignore[arg-type]

    assert tool.manifest.name == "research.analyze_ticker"
    assert tool.manifest.scope == "research"
    assert tool.manifest.side_effect == ToolSideEffect.READ_ONLY
    assert "broker_account" not in tool.manifest.requires_setup


def test_analyze_ticker_tool_requires_setup_and_allowed_interface() -> None:
    analyzer = FakeAnalyzer()
    tool = analyze_ticker_tool(analyzer)  # type: ignore[arg-type]

    blocked = tool.execute(
        {"ticker": "NVDA"},
        ToolContext(interface="trader_bot", setup=frozenset({"llm", "market_data"})),
    )
    missing_setup = tool.execute(
        {"ticker": "NVDA"},
        ToolContext(interface="research_chat", setup=frozenset({"llm"})),
    )
    ok = tool.execute(
        {"ticker": "nvda"},
        ToolContext(interface="research_chat", setup=frozenset({"llm", "market_data"})),
    )

    assert blocked.ok is False
    assert missing_setup.ok is False
    assert ok.ok is True
    assert ok.data["ticker"] == "NVDA"
    assert analyzer.last_ticker == "NVDA"


def test_registry_lists_only_tools_available_for_context() -> None:
    registry = ToolRegistry([analyze_ticker_tool(FakeAnalyzer())])  # type: ignore[arg-type]

    none = registry.available_for(ToolContext(interface="research_chat"))
    available = registry.available_for(
        ToolContext(interface="research_chat", setup=frozenset({"llm", "market_data"})),
    )

    assert none == []
    assert [m.name for m in available] == ["research.analyze_ticker"]

