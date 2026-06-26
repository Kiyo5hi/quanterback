from __future__ import annotations

from dataclasses import dataclass

from quanterback.domain.decision import StrategyDecision
from quanterback.tools.capabilities import CapabilityCatalog, CapabilitySelection
from quanterback.tools.registry import ToolContext
from quanterback.tools.research import analyze_ticker_tool


class FakeSummary:
    def model_dump(self, mode: str = "python"):
        return {"ticker": "NVDA"}


@dataclass
class FakeAnalysis:
    ticker: str
    summary: object
    decision: StrategyDecision
    model_name: str = "fake"


class FakeAnalyzer:
    def analyze_ticker(self, ticker: str) -> FakeAnalysis:
        return FakeAnalysis(
            ticker=ticker,
            summary=FakeSummary(),
            decision=StrategyDecision(
                action="PASS",
                ticker=ticker,
                strategy="MOMENTUM",
                params=None,
                rationale="Research-only analysis says this ticker is not compelling.",
                confidence=0.5,
            ),
        )


def test_capability_selection_expands_capabilities_to_tools() -> None:
    selection = CapabilitySelection(
        enabled=("research.watchlist",),
        include_tools=("research.analyze_ticker",),
        exclude_tools=("research.watchlist_remove",),
    )

    assert selection.resolve_tool_names() == (
        "research.analyze_ticker",
        "research.watchlist_add",
        "research.watchlist_list",
    )


def test_capability_selection_from_toml_shape() -> None:
    selection = CapabilitySelection.from_toml({
        "capabilities": {
            "enabled": ["research.analyze_ticker", "research.digest_jobs"],
        },
        "tools": {
            "include": ["research.watchlist_list"],
            "exclude": ["research.cancel_digest"],
        },
    })

    assert "research.analyze_ticker" in selection.resolve_tool_names()
    assert "research.schedule_digest" in selection.resolve_tool_names()
    assert "research.cancel_digest" not in selection.resolve_tool_names()
    assert "research.watchlist_list" in selection.resolve_tool_names()


def test_catalog_builds_registry_for_selected_tools() -> None:
    catalog = CapabilityCatalog()
    catalog.register(analyze_ticker_tool(FakeAnalyzer()))  # type: ignore[arg-type]
    selection = CapabilitySelection(enabled=("research.analyze_ticker",))

    registry = catalog.registry_for(selection)
    result = registry.get("research.analyze_ticker").execute(
        {"ticker": "nvda"},
        ToolContext(interface="research_chat", setup=frozenset({"llm", "market_data"})),
    )

    assert result.ok is True
    assert result.data["ticker"] == "NVDA"
    assert catalog.unknown_tool_names(selection) == ()


def test_catalog_reports_unknown_selected_tools() -> None:
    catalog = CapabilityCatalog()
    selection = CapabilitySelection(enabled=("research.watchlist",))

    assert catalog.unknown_tool_names(selection) == (
        "research.watchlist_add",
        "research.watchlist_list",
        "research.watchlist_remove",
    )

