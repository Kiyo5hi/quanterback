from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.domain.decision import StrategyDecision
from quanterback.domain.market import MarketDataQualityError
from quanterback.tools.registry import ToolContext, ToolRegistry, ToolSideEffect
from quanterback.tools.research import (
    analyze_ticker_tool,
    cancel_digest_tool,
    list_jobs_tool,
    schedule_digest_tool,
    watchlist_add_tool,
    watchlist_list_tool,
    watchlist_remove_tool,
)


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


class BadDataAnalyzer:
    def analyze_ticker(self, ticker: str) -> FakeAnalysis:
        raise MarketDataQualityError("last close unavailable or non-positive")


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


def test_analyze_ticker_tool_canonicalizes_common_names() -> None:
    analyzer = FakeAnalyzer()
    tool = analyze_ticker_tool(analyzer)  # type: ignore[arg-type]

    nvidia = tool.execute(
        {"ticker": "Nvidia"},
        ToolContext(interface="research_chat", setup=frozenset({"llm", "market_data"})),
    )
    assert nvidia.ok is True
    assert analyzer.last_ticker == "NVDA"

    sox = tool.execute(
        {"ticker": "SOX"},
        ToolContext(interface="research_chat", setup=frozenset({"llm", "market_data"})),
    )
    assert sox.ok is True
    assert analyzer.last_ticker == "SOXX"


def test_analyze_ticker_tool_returns_friendly_data_quality_error() -> None:
    tool = analyze_ticker_tool(BadDataAnalyzer())  # type: ignore[arg-type]

    result = tool.execute(
        {"ticker": "SPCX"},
        ToolContext(interface="research_chat", setup=frozenset({"llm", "market_data"})),
    )

    assert result.ok is False
    assert "没法可靠分析 SPCX" in result.message
    assert result.data["error_type"] == "market_data_quality"


def test_registry_lists_only_tools_available_for_context() -> None:
    registry = ToolRegistry([analyze_ticker_tool(FakeAnalyzer())])  # type: ignore[arg-type]

    none = registry.available_for(ToolContext(interface="research_chat"))
    available = registry.available_for(
        ToolContext(interface="research_chat", setup=frozenset({"llm", "market_data"})),
    )

    assert none == []
    assert [m.name for m in available] == ["research.analyze_ticker"]


def test_watchlist_tools_use_authenticated_context_user(tmp_path) -> None:
    store = SqliteStore(tmp_path / "q.sqlite")
    alice = store.research_upsert_user(provider="telegram", external_user_id="1")
    bob = store.research_upsert_user(provider="telegram", external_user_id="2")
    assert alice.id is not None
    assert bob.id is not None
    context = ToolContext(
        interface="research_chat",
        user_id=str(alice.id),
        setup=frozenset({"research_store"}),
    )

    add = watchlist_add_tool(store).execute(
        {"ticker": "nvda", "user_id": bob.id}, context,
    )
    listed = watchlist_list_tool(store).execute({}, context)
    removed = watchlist_remove_tool(store).execute({"ticker": "NVDA"}, context)

    assert add.ok is True
    assert listed.data["tickers"] == ["NVDA"]
    assert removed.data["removed"] is True
    assert store.research_list_watchlist_items(bob.id) == []


def test_watchlist_tools_require_user_context(tmp_path) -> None:
    store = SqliteStore(tmp_path / "q.sqlite")
    result = watchlist_add_tool(store).execute(
        {"ticker": "NVDA"},
        ToolContext(interface="research_chat", setup=frozenset({"research_store"})),
    )

    assert result.ok is False
    assert "user context" in result.message


def test_digest_job_tools_schedule_list_and_cancel(tmp_path) -> None:
    store = SqliteStore(tmp_path / "q.sqlite")
    user = store.research_upsert_user(provider="telegram", external_user_id="1")
    assert user.id is not None
    context = ToolContext(
        interface="research_chat",
        user_id=str(user.id),
        timezone="America/Los_Angeles",
        setup=frozenset({"research_store"}),
    )

    scheduled = schedule_digest_tool(store).execute(
        {
            "schedule_kind": "daily",
            "schedule_spec": {"hour": 8, "minute": 30},
            "next_run_at": datetime(2026, 6, 26, 15, tzinfo=timezone.utc).isoformat(),
        },
        context,
    )
    listed = list_jobs_tool(store).execute({}, context)
    cancelled = cancel_digest_tool(store).execute(
        {"job_id": scheduled.data["job_id"]}, context,
    )
    listed_after_cancel = list_jobs_tool(store).execute({}, context)

    assert scheduled.ok is True
    assert listed.data["count"] == 1
    assert listed.data["jobs"][0]["timezone"] == "America/Los_Angeles"
    assert cancelled.data["cancelled"] is True
    assert listed_after_cancel.data["count"] == 0


def test_digest_schedule_requires_confirmation_metadata(tmp_path) -> None:
    store = SqliteStore(tmp_path / "q.sqlite")
    tool = schedule_digest_tool(store)

    assert tool.manifest.requires_confirmation is True
    assert tool.manifest.side_effect == ToolSideEffect.USER_WRITE


def test_digest_schedule_requires_registry_confirmation(tmp_path) -> None:
    store = SqliteStore(tmp_path / "q.sqlite")
    user = store.research_upsert_user(provider="telegram", external_user_id="1")
    assert user.id is not None
    registry = ToolRegistry([schedule_digest_tool(store)])
    context = ToolContext(
        interface="research_chat",
        user_id=str(user.id),
        setup=frozenset({"research_store"}),
    )

    blocked = registry.execute("research.schedule_digest", {}, context)
    jobs_after_block = store.research_list_scheduled_jobs(user.id)
    ok = registry.execute("research.schedule_digest", {}, context, confirmed=True)

    assert blocked.ok is False
    assert blocked.data["confirmation_required"] is True
    assert jobs_after_block == []
    assert ok.ok is True
    assert store.research_list_scheduled_jobs(user.id) != []
