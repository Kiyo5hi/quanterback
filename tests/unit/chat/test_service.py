from __future__ import annotations

from datetime import datetime, timezone

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.chat.intent import LLMIntentResolver
from quanterback.chat.models import ChatRequest
from quanterback.chat.service import ResearchChatService
from quanterback.interfaces.decision import ChatMessage, ChatResponse
from quanterback.ticker_resolver import TickerResolver
from quanterback.tools.capabilities import CapabilitySelection, build_research_catalog
from quanterback.tools.registry import (
    Tool,
    ToolContext,
    ToolManifest,
    ToolRegistry,
    ToolResult,
    ToolSideEffect,
)


def _request(text: str) -> ChatRequest:
    return ChatRequest(
        provider="telegram",
        external_user_id="u1",
        external_chat_id="c1",
        message_id=1,
        text=text,
        display_name="Alice",
        received_at=datetime.now(tz=timezone.utc),
    )


def _service(tmp_path) -> tuple[ResearchChatService, SqliteStore]:
    store = SqliteStore(tmp_path / "q.sqlite")
    catalog = build_research_catalog(store=store)
    registry = catalog.registry_for(CapabilitySelection(
        enabled=("research.watchlist", "research.digest_jobs"),
    ))
    return ResearchChatService(
        store=store,
        registry=registry,
        language="zh",
        timezone="Asia/Shanghai",
    ), store


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        response_schema: dict | None = None,
        temperature: float = 0.0,
    ) -> ChatResponse:
        self.calls += 1
        return ChatResponse(content=self.content, model="fake", usage={})


def test_chat_service_routes_watchlist_commands_per_user(tmp_path) -> None:
    service, store = _service(tmp_path)

    add = service.handle(_request("/add nvda"))
    listed = service.handle(_request("/watchlist"))
    users = store._conn.execute("SELECT COUNT(*) FROM research_users").fetchone()[0]

    assert add.ok is True
    assert "NVDA" in listed.text
    assert users == 1


def test_chat_service_confirmation_flow_for_digest(tmp_path) -> None:
    service, store = _service(tmp_path)

    first = service.handle(_request("/digest daily 08:00"))
    jobs_after_first = store._conn.execute(
        "SELECT COUNT(*) FROM research_scheduled_jobs"
    ).fetchone()[0]
    confirmed = service.handle(_request("确认"))
    jobs_after_confirm = store._conn.execute(
        "SELECT COUNT(*) FROM research_scheduled_jobs"
    ).fetchone()[0]

    assert first.confirmation_required is True
    assert jobs_after_first == 0
    assert confirmed.ok is True
    assert jobs_after_confirm == 1


def test_chat_service_reports_disabled_tool(tmp_path) -> None:
    store = SqliteStore(tmp_path / "q.sqlite")
    service = ResearchChatService(
        store=store,
        registry=build_research_catalog(store=store).registry_for(
            CapabilitySelection(enabled=("research.watchlist",)),
        ),
    )

    reply = service.handle(_request("/analyze NVDA"))

    assert reply.ok is False
    assert "没有启用工具" in reply.text


def test_chat_service_help_is_human_readable(tmp_path) -> None:
    service, _store = _service(tmp_path)

    reply = service.handle(_request("你能干嘛"))

    assert reply.ok is True
    assert "QuanterChat" in reply.text
    assert "research.watchlist_add" not in reply.text
    assert "帮我关注" in reply.text


def test_chat_service_greeting_is_local_and_human_readable(tmp_path) -> None:
    service, _store = _service(tmp_path)

    reply = service.handle(_request("你好"))

    assert reply.ok is True
    assert "QuanterChat" in reply.text
    assert "分析 NVDA" in reply.text


def test_chat_service_unknown_reply_gives_next_steps(tmp_path) -> None:
    service, _store = _service(tmp_path)

    reply = service.handle(_request("今天心情怎么样"))

    assert reply.ok is False
    assert "我没太理解" in reply.text
    assert "分析 NVDA" in reply.text


def test_chat_service_routes_natural_language_with_llm_intent(tmp_path) -> None:
    store = SqliteStore(tmp_path / "q.sqlite")
    catalog = build_research_catalog(store=store)
    registry = catalog.registry_for(CapabilitySelection(enabled=("research.watchlist",)))
    llm = FakeLLM(
        '{"kind":"tool","tool_name":"research.watchlist_add",'
        '"params":{"ticker":"NVDA"},"confidence":0.91}'
    )
    service = ResearchChatService(
        store=store,
        registry=registry,
        intent_resolver=LLMIntentResolver(llm),
    )

    reply = service.handle(_request("帮我关注一下英伟达 NVDA"))
    listed = service.handle(_request("/watchlist"))

    assert reply.ok is True
    assert llm.calls == 1
    assert "NVDA" in listed.text


def test_chat_service_formats_analysis_result(tmp_path) -> None:
    service, _store = _service(tmp_path)

    reply = service._render_result(ToolResult(
        ok=True,
        message="fallback",
        data={
            "ticker": "MU",
            "action": "PASS",
            "confidence": 0.42,
            "rationale": "技术趋势不错，但专家共识不足，因此先观察。",
            "summary": {
                "price": {
                    "last_close": 123.45,
                    "return_1d": 0.012,
                    "return_5d": 0.034,
                    "return_20d": 0.08,
                },
                "volatility": {"atr_pct_of_price": 0.041, "regime": "normal"},
                "volume": {"volume_ratio": 1.7, "regime": "elevated"},
                "technicals": {"rsi_14": 61.2, "macd_signal": "bullish_cross"},
            },
            "decision": {
                "agent_debate": {
                    "fundamentalist": {
                        "lean": "neutral",
                        "confidence": 0.5,
                        "key_points": ["估值数据不足"],
                        "rationale": "基本面没有强信号",
                    },
                    "technician": {
                        "lean": "bullish",
                        "confidence": 0.7,
                        "key_points": ["价格站上主要均线"],
                        "rationale": "趋势偏强",
                    },
                    "sentiment": None,
                },
            },
        },
    ))

    assert "MU 研究结果" in reply.text
    assert "关键指标:" in reply.text
    assert "专家观点:" in reply.text
    assert "综合理由:" in reply.text
    assert "结论: PASS    置信度" not in reply.text
    assert "这是研究结论" not in reply.text
    assert "MU — PASS" not in reply.text


def test_chat_service_runs_multiple_ticker_analyses_from_one_message(tmp_path) -> None:
    store = SqliteStore(tmp_path / "q.sqlite")
    calls: list[str] = []

    def analyze(params: dict, _context: ToolContext) -> ToolResult:
        ticker = str(params["ticker"])
        calls.append(ticker)
        return ToolResult(
            ok=True,
            data={
                "ticker": ticker,
                "action": "PASS",
                "rationale": f"{ticker} rationale",
                "summary": {},
                "decision": {},
            },
        )

    registry = ToolRegistry([
        Tool(
            manifest=ToolManifest(
                name="research.analyze_ticker",
                description="Analyze one ticker",
                input_schema={
                    "type": "object",
                    "properties": {"ticker": {"type": "string"}},
                    "required": ["ticker"],
                },
                side_effect=ToolSideEffect.READ_ONLY,
                scope="research",
            ),
            handler=analyze,
        ),
    ])
    service = ResearchChatService(store=store, registry=registry)

    reply = service.handle(_request("分别分析 tsla 和 spcx"))

    assert calls == ["TSLA", "SPCX"]
    assert "TSLA 研究结果" in reply.text
    assert "SPCX 研究结果" in reply.text
    assert "---" in reply.text


def test_chat_service_asks_user_to_choose_ambiguous_ticker(tmp_path) -> None:
    store = SqliteStore(tmp_path / "q.sqlite")
    calls: list[str] = []

    def analyze(params: dict, _context: ToolContext) -> ToolResult:
        ticker = str(params["ticker"])
        calls.append(ticker)
        return ToolResult(
            ok=True,
            data={"ticker": ticker, "action": "PASS", "rationale": "ok"},
        )

    registry = ToolRegistry([
        Tool(
            manifest=ToolManifest(
                name="research.analyze_ticker",
                description="Analyze one ticker",
                input_schema={
                    "type": "object",
                    "properties": {"ticker": {"type": "string"}},
                    "required": ["ticker"],
                },
                side_effect=ToolSideEffect.READ_ONLY,
                scope="research",
            ),
            handler=analyze,
        ),
    ])
    service = ResearchChatService(
        store=store,
        registry=registry,
        ticker_resolver=TickerResolver(search_fn=lambda _q, _limit: []),
    )

    first = service.handle(_request("分析阿里"))
    second = service.handle(_request("港股"))

    assert first.ok is False
    assert "BABA" in first.text
    assert "9988.HK" in first.text
    assert calls == ["9988.HK"]
    assert "9988.HK 研究结果" in second.text


def test_chat_service_blocks_unresolved_llm_guessed_ticker(tmp_path) -> None:
    store = SqliteStore(tmp_path / "q.sqlite")
    registry = ToolRegistry([
        Tool(
            manifest=ToolManifest(
                name="research.analyze_ticker",
                description="Analyze one ticker",
                input_schema={
                    "type": "object",
                    "properties": {"ticker": {"type": "string"}},
                    "required": ["ticker"],
                },
                side_effect=ToolSideEffect.READ_ONLY,
                scope="research",
            ),
            handler=lambda _params, _context: ToolResult(ok=False, message="should not run"),
        ),
    ])
    llm = FakeLLM(
        '{"kind":"tool","tool_name":"research.analyze_ticker",'
        '"params":{"ticker":"ZHIPU"},"confidence":0.91}'
    )
    service = ResearchChatService(
        store=store,
        registry=registry,
        intent_resolver=LLMIntentResolver(llm),
        ticker_resolver=TickerResolver(
            search_fn=lambda _q, _limit: [
                # Search may return irrelevant crypto-like noise; it should be ignored.
            ]
        ),
    )

    reply = service.handle(_request("分析智谱股票"))

    assert reply.ok is False
    assert "没找到" in reply.text
    assert "Zhipu" in reply.text
