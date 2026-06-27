from __future__ import annotations

from dataclasses import dataclass, field

from quanterback.chat.intent import LLMIntentResolver
from quanterback.chat.models import ChatIntent, ChatReply, ChatRequest
from quanterback.chat.router import ResearchChatRouter
from quanterback.interfaces.research_store import ResearchStore
from quanterback.tools.registry import ToolContext, ToolRegistry, ToolResult


@dataclass
class PendingToolCall:
    tool_name: str
    params: dict


@dataclass
class ResearchChatService:
    store: ResearchStore
    registry: ToolRegistry
    router: ResearchChatRouter = field(default_factory=ResearchChatRouter)
    intent_resolver: LLMIntentResolver | None = None
    interface: str = "research_chat"
    setup: frozenset[str] = field(
        default_factory=lambda: frozenset({"research_store", "market_data", "llm"})
    )
    language: str = "zh"
    timezone: str = "UTC"
    pending: dict[str, PendingToolCall] = field(default_factory=dict)

    def handle(self, request: ChatRequest) -> ChatReply:
        user = self.store.research_upsert_user(
            provider=request.provider,
            external_user_id=request.external_user_id,
            external_chat_id=request.external_chat_id,
            display_name=request.display_name,
            timezone_name=self.timezone,
            locale=self.language,
        )
        assert user.id is not None
        key = self._pending_key(request.provider, request.external_user_id, request.external_chat_id)
        intent = self.router.route(request.text)
        context = self._tool_context(user.id)
        if intent.kind == "unknown" and not request.text.strip().startswith("/"):
            intent = self._resolve_natural_intent(request.text, context)

        if intent.kind == "confirm":
            pending = self.pending.pop(key, None)
            if pending is None:
                return ChatReply(text="没有等待确认的操作。")
            return self._execute(
                pending.tool_name, pending.params, user_id=user.id, confirmed=True,
            )
        if intent.kind == "cancel":
            existed = self.pending.pop(key, None) is not None
            return ChatReply(text="已取消。" if existed else "没有等待取消的操作。")
        if intent.kind == "help":
            return ChatReply(text=self.help_text())
        if intent.kind != "tool" or not intent.tool_name:
            return ChatReply(text=self.help_text(), ok=False)

        reply = self._execute(intent.tool_name, intent.params, user_id=user.id, confirmed=False)
        if reply.confirmation_required:
            self.pending[key] = PendingToolCall(intent.tool_name, intent.params)
        return reply

    def _resolve_natural_intent(self, text: str, context: ToolContext) -> ChatIntent:
        manifests = self.registry.available_for(context)
        if self.intent_resolver is not None:
            intent = self.intent_resolver.resolve(text, manifests)
            if intent.kind != "unknown":
                return intent
        return self.router.route_natural_fallback(text)

    def _tool_context(self, user_id: int) -> ToolContext:
        return ToolContext(
            interface=self.interface,
            user_id=str(user_id),
            chat_id=None,
            message_id=0,
            language=self.language,
            timezone=self.timezone,
            setup=self.setup,
        )

    def _execute(
        self, tool_name: str, params: dict, *, user_id: int, confirmed: bool,
    ) -> ChatReply:
        context = self._tool_context(user_id)
        try:
            result = self.registry.execute(
                tool_name, params, context, confirmed=confirmed,
            )
        except KeyError:
            return ChatReply(
                ok=False,
                text=f"这个部署没有启用工具: {tool_name}",
            )
        return self._render_result(result)

    def _render_result(self, result: ToolResult) -> ChatReply:
        if result.data.get("confirmation_required"):
            return ChatReply(
                ok=False,
                confirmation_required=True,
                text=(
                    f"需要确认才能执行 `{result.data.get('tool')}`。\n"
                    "回复 `确认` 执行，或回复 `取消` 放弃。"
                ),
            )
        if result.data.get("action") and result.data.get("ticker"):
            return ChatReply(
                ok=result.ok,
                text=(
                    f"{result.data['ticker']} — {result.data['action']} "
                    f"({float(result.data.get('confidence', 0.0)):.2f})\n"
                    f"{result.data.get('rationale') or result.message}"
                ),
            )
        return ChatReply(ok=result.ok, text=result.message or str(result.data))

    def help_text(self) -> str:
        manifests = self.registry.available_for(ToolContext(
            interface=self.interface,
            user_id="0",
            setup=self.setup,
        ))
        names = "\n".join(f"- `{m.name}`" for m in manifests)
        return (
            "可用能力：\n"
            f"{names or '- 当前没有启用工具'}\n\n"
            "快捷命令：\n"
            "`/analyze NVDA`, `/add NVDA`, `/remove NVDA`, `/watchlist`, "
            "`/digest daily 08:00`, `/jobs`, `/cancel 1`"
        )

    @staticmethod
    def _pending_key(provider: str, user_id: str, chat_id: str) -> str:
        return f"{provider}:{user_id}:{chat_id}"
