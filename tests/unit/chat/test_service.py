from __future__ import annotations

from datetime import datetime, timezone

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.chat.intent import LLMIntentResolver
from quanterback.chat.models import ChatRequest
from quanterback.chat.service import ResearchChatService
from quanterback.interfaces.decision import ChatMessage, ChatResponse
from quanterback.tools.capabilities import CapabilitySelection, build_research_catalog


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
