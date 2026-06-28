from __future__ import annotations

import logging
from dataclasses import dataclass, field

from quanterback.chat.intent import LLMIntentResolver
from quanterback.chat.models import ChatIntent, ChatReply, ChatRequest
from quanterback.chat.router import ResearchChatRouter
from quanterback.interfaces.research_store import ResearchStore
from quanterback.ticker_resolver import TickerCandidate, TickerResolver
from quanterback.tickers import extract_tickers
from quanterback.tools.registry import ToolContext, ToolRegistry, ToolResult

log = logging.getLogger(__name__)


@dataclass
class PendingToolCall:
    tool_name: str
    params: dict


@dataclass
class PendingTickerChoice:
    tool_name: str
    params: dict
    query: str
    candidates: tuple[TickerCandidate, ...]


@dataclass
class ResearchChatService:
    store: ResearchStore
    registry: ToolRegistry
    router: ResearchChatRouter = field(default_factory=ResearchChatRouter)
    intent_resolver: LLMIntentResolver | None = None
    ticker_resolver: TickerResolver = field(default_factory=TickerResolver)
    interface: str = "research_chat"
    setup: frozenset[str] = field(
        default_factory=lambda: frozenset({"research_store", "market_data", "llm"})
    )
    language: str = "zh"
    timezone: str = "UTC"
    pending: dict[str, PendingToolCall] = field(default_factory=dict)
    pending_ticker_choices: dict[str, PendingTickerChoice] = field(default_factory=dict)

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
        key = self._pending_key(
            request.provider,
            request.external_user_id,
            request.external_chat_id,
        )
        pending_choice = self.pending_ticker_choices.get(key)
        if pending_choice is not None:
            selected = _select_ticker_candidate(request.text, pending_choice.candidates)
            if selected is not None:
                self.pending_ticker_choices.pop(key, None)
                params = {**pending_choice.params, "ticker": selected.symbol}
                return self._execute(
                    pending_choice.tool_name,
                    params,
                    user_id=user.id,
                    confirmed=False,
                )
            if request.text.strip().lower() in {"取消", "cancel", "no", "n"}:
                self.pending_ticker_choices.pop(key, None)
                return ChatReply(text="已取消。")
            return ChatReply(text=_render_ticker_choices(pending_choice), ok=False)

        intent = self.router.route(request.text)
        context = self._tool_context(user.id)
        if intent.kind == "unknown" and not request.text.strip().startswith("/"):
            if _looks_like_local_reply(request.text):
                log.info(
                    "Chat routed interface=%s user=%s kind=local tool=None params={} text=%r",
                    self.interface,
                    request.external_user_id,
                    request.text[:160],
                )
                return ChatReply(text=self.unknown_text(request.text), ok=True)
            intent = self._resolve_natural_intent(request.text, context)
        log.info(
            "Chat routed interface=%s user=%s kind=%s tool=%s params=%s text=%r",
            self.interface,
            request.external_user_id,
            intent.kind,
            intent.tool_name,
            _redact_params(intent.params),
            request.text[:160],
        )

        if intent.kind == "confirm":
            pending = self.pending.pop(key, None)
            if pending is None:
                return ChatReply(text="没有等待确认的操作。")
            return self._execute(
                pending.tool_name, pending.params, user_id=user.id, confirmed=True,
            )
        if intent.kind == "cancel":
            existed = self.pending.pop(key, None) is not None
            existed = self.pending_ticker_choices.pop(key, None) is not None or existed
            return ChatReply(text="已取消。" if existed else "没有等待取消的操作。")
        if intent.kind == "help":
            return ChatReply(text=self.help_text())
        if intent.kind != "tool" or not intent.tool_name:
            return ChatReply(text=self.unknown_text(request.text), ok=False)

        if intent.tool_name == "research.analyze_ticker":
            tickers = extract_tickers(request.text)
            if len(tickers) > 1:
                reply = self._execute_many_analyses(tickers, user_id=user.id)
                return reply
            resolved = self.ticker_resolver.resolve(
                request.text,
                proposed_ticker=str(intent.params.get("ticker") or ""),
            )
            if resolved.ambiguous:
                pending_choice = PendingTickerChoice(
                    tool_name=intent.tool_name,
                    params=intent.params,
                    query=resolved.query,
                    candidates=resolved.candidates,
                )
                self.pending_ticker_choices[key] = pending_choice
                return ChatReply(text=_render_ticker_choices(pending_choice), ok=False)
            if not resolved.found:
                return ChatReply(text=_ticker_not_found_text(resolved.query), ok=False)
            if resolved.ticker:
                intent = ChatIntent(
                    kind="tool",
                    tool_name=intent.tool_name,
                    params={**intent.params, "ticker": resolved.ticker},
                    confidence=intent.confidence,
                )

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
        except Exception as exc:
            log.exception(
                "Tool execution failed: tool=%s params=%s",
                tool_name,
                _redact_params(params),
            )
            return ChatReply(
                ok=False,
                text=(
                    "我刚才尝试执行这个请求，但后端能力报错了。\n"
                    f"原因：{_friendly_error(exc)}\n\n"
                    "你可以换一个更常见的美股 ticker 试试，比如 `分析 NVDA`，"
                    "或者直接发 `我的自选` 看看当前列表。"
                ),
            )
        return self._render_result(result)

    def _execute_many_analyses(self, tickers: list[str], *, user_id: int) -> ChatReply:
        rendered: list[str] = []
        ok = True
        for ticker in tickers:
            reply = self._execute(
                "research.analyze_ticker",
                {"ticker": ticker},
                user_id=user_id,
                confirmed=False,
            )
            ok = ok and reply.ok
            rendered.append(reply.text)
        return ChatReply(ok=ok, text="\n\n---\n\n".join(rendered))

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
                text=_render_analysis_result(result),
            )
        return ChatReply(ok=result.ok, text=result.message or str(result.data))

    def help_text(self) -> str:
        manifests = self.registry.available_for(ToolContext(
            interface=self.interface,
            user_id="0",
            setup=self.setup,
        ))
        tool_names = {m.name for m in manifests}
        if self.interface == "trader_bot":
            return (
                "我是私有交易控制 bot，主要帮你操作这套 QuanterBack 部署。\n\n"
                "你可以直接这样说：\n"
                "- `看一下现在状态`\n"
                "- `preview SPCX`\n"
                "- `把 NVDA 加进 watchlist`\n"
                "- `扫一下 SOXX`\n\n"
                "会影响真实交易流程的动作，我会先让你确认。"
            )
        lines = [
            "我是 QuanterChat，偏研究助手，不会替你下单。",
            "",
            "你可以直接用自然语言跟我说：",
        ]
        if "research.analyze_ticker" in tool_names:
            lines.append("- `帮我分析一下 NVDA`：看价格、新闻、基本面和模型判断")
            lines.append("- `分析小米` 或 `分析 1810.HK`：港股按 Yahoo ticker 格式处理")
            lines.append("- `分别分析 TSLA 和 SPCX`：逐只研究后合并回复")
        if "research.watchlist_add" in tool_names:
            lines.append("- `帮我关注 SOXX`：加入你的个人自选")
        if "research.watchlist_list" in tool_names:
            lines.append("- `我的自选有哪些`：查看你自己的 watchlist")
        if "research.schedule_digest" in tool_names:
            lines.append("- `每天早上给我一份日报`：创建定时研究简报")
        lines.extend([
            "",
            "我现在最擅长的是“单只股票研究”和“维护你的个人自选”。",
            "如果你只是问泛泛的问题，我会尽量说明我缺什么信息。",
        ])
        return "\n".join(lines)

    def unknown_text(self, text: str) -> str:
        if _looks_like_greeting(text):
            if self.interface == "trader_bot":
                return (
                    "你好，我是 QuanterBack 的私有控制 bot。\n\n"
                    "我可以帮你看交易状态、preview 某只股票、管理 trader watchlist。"
                    "你可以直接说：`看状态` 或 `preview NVDA`。"
                )
            return (
                "你好，我是 QuanterChat，主要帮你做美股研究和个人自选管理。\n\n"
                "你可以直接说：`分析 NVDA`、`把 SOXX 加到自选`，"
                "或者问 `我的自选有哪些`。"
            )
        if _looks_like_capability_question(text):
            return self.help_text()
        if self.interface == "trader_bot":
            return (
                "我没确定你想让我做哪个交易控制动作。\n\n"
                "你可以说得更具体一点，比如 `看状态`、`preview NVDA`、"
                "`把 NVDA 加进 watchlist`。涉及真实 scan 或控制开关时，我会要求确认。"
            )
        return (
            "我没太理解你要我研究什么。\n\n"
            "现在我需要一个比较明确的目标，比如股票代码或 watchlist 动作：\n"
            "- `分析 NVDA`\n"
            "- `把 SOXX 加到自选`\n"
            "- `我的自选有哪些`\n"
            "- `每天早上给我一份日报`\n\n"
            "我目前不是通用闲聊机器人，主要做美股研究和个人 watchlist。"
        )

    @staticmethod
    def _pending_key(provider: str, user_id: str, chat_id: str) -> str:
        return f"{provider}:{user_id}:{chat_id}"


def _looks_like_capability_question(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "what can you do",
            "help",
            "usage",
            "怎么用",
            "能干嘛",
            "能做什么",
            "你是谁",
            "介绍一下",
            "怎么玩",
            "使用说明",
            "支持什么",
            "支持哪些",
            "能看",
            "港股",
        )
    )


def _looks_like_greeting(text: str) -> bool:
    normalized = text.lower().strip(" \t\r\n.!?。！？～~")
    return normalized in {
        "hi",
        "hello",
        "hey",
        "你好",
        "您好",
        "嗨",
        "在吗",
        "在不在",
    }


def _looks_like_local_reply(text: str) -> bool:
    return _looks_like_greeting(text) or _looks_like_capability_question(text)


def _render_ticker_choices(choice: PendingTickerChoice) -> str:
    lines = [
        "这家公司可能在多个市场交易，我不能默认替你选。",
        "请明确选择你要分析的 ticker：",
        "",
    ]
    for idx, candidate in enumerate(choice.candidates, start=1):
        lines.append(f"{idx}. {candidate.label()}")
    lines.extend([
        "",
        "回复序号、`港股`、`美股`、`OTC`，或直接回复 ticker。",
    ])
    return "\n".join(lines)


def _select_ticker_candidate(
    text: str, candidates: tuple[TickerCandidate, ...],
) -> TickerCandidate | None:
    raw = text.strip()
    lowered = raw.lower()
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(candidates):
            return candidates[idx - 1]
    if lowered in {"港股", "香港", "hk", "hong kong"}:
        return _first_candidate_matching(candidates, lambda c: c.symbol.endswith(".HK"))
    if lowered in {"美股", "美国", "us", "usa", "nyse", "nasdaq"}:
        return _first_candidate_matching(
            candidates,
            lambda c: c.exchange.upper() in {"NYSE", "NASDAQ"} or "." not in c.symbol,
        )
    if lowered in {"otc", "粉单", "场外"}:
        return _first_candidate_matching(
            candidates,
            lambda c: c.exchange.upper() in {"OTC MARKETS", "PNK"},
        )
    for candidate in candidates:
        if lowered == candidate.symbol.lower():
            return candidate
    return None


def _first_candidate_matching(
    candidates: tuple[TickerCandidate, ...],
    predicate,
) -> TickerCandidate | None:
    for candidate in candidates:
        if predicate(candidate):
            return candidate
    return None


def _ticker_not_found_text(query: str) -> str:
    if query:
        return (
            f"我没找到 `{query}` 对应的可用股票 ticker。\n\n"
            "它可能还没有上市、不是 Yahoo Finance 支持的标的，"
            "或者需要你直接给我交易所代码。"
        )
    return "我没有识别到可用的股票代码。你可以直接发 ticker，比如 `NVDA` 或 `1810.HK`。"


def _friendly_error(exc: Exception) -> str:
    message = str(exc).strip()
    if "last close unavailable" in message or "bad price data" in message:
        return (
            "行情源没有拿到可用的最新收盘价，可能是 ticker 不对、"
            "数据源暂时缺数据，或这个标的不适合当前分析流程。"
        )
    if "ticker is required" in message:
        return "我没有识别到股票代码。"
    return message[:220] or exc.__class__.__name__


def _render_analysis_result(result: ToolResult) -> str:
    data = result.data
    ticker = str(data.get("ticker") or "")
    action = str(data.get("action") or "")
    confidence = _fmt_float(data.get("confidence"), digits=2)
    rationale = str(data.get("rationale") or result.message or "").strip()
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    decision = data.get("decision") if isinstance(data.get("decision"), dict) else {}

    lines = [
        f"{ticker} 研究结果",
        "",
        f"结论: {action}" if action == "PASS" else f"结论: {action}    置信度: {confidence}",
    ]

    metrics = _analysis_metric_lines(summary)
    if metrics:
        lines.extend(["", "关键指标:", *metrics])

    expert_lines = _expert_lines(decision)
    if expert_lines:
        lines.extend(["", "专家观点:", *expert_lines])

    if rationale:
        lines.extend(["", "综合理由:", _wrap_text(rationale, limit=520)])

    return "\n".join(lines)


def _analysis_metric_lines(summary: dict) -> list[str]:
    price = summary.get("price") if isinstance(summary.get("price"), dict) else {}
    volume = summary.get("volume") if isinstance(summary.get("volume"), dict) else {}
    volatility = (
        summary.get("volatility")
        if isinstance(summary.get("volatility"), dict)
        else {}
    )
    technicals = (
        summary.get("technicals")
        if isinstance(summary.get("technicals"), dict)
        else {}
    )
    out: list[str] = []
    last_close = _fmt_float(price.get("last_close"), digits=2)
    ret_1d = _fmt_pct(price.get("return_1d"))
    ret_5d = _fmt_pct(price.get("return_5d"))
    ret_20d = _fmt_pct(price.get("return_20d"))
    if last_close != "n/a":
        out.append(f"- 价格: {last_close}  |  1日 {ret_1d} / 5日 {ret_5d} / 20日 {ret_20d}")
    atr_pct = _fmt_pct(volatility.get("atr_pct_of_price"))
    vol_regime = volatility.get("regime") or "n/a"
    if atr_pct != "n/a" or vol_regime != "n/a":
        out.append(f"- 波动: ATR/价格 {atr_pct}  |  regime {vol_regime}")
    volume_ratio = _fmt_float(volume.get("volume_ratio"), digits=2)
    volume_regime = volume.get("regime") or "n/a"
    if volume_ratio != "n/a" or volume_regime != "n/a":
        ratio = f"{volume_ratio}x" if volume_ratio != "n/a" else "n/a"
        out.append(f"- 成交量: {ratio}  |  regime {volume_regime}")
    rsi = _fmt_float(technicals.get("rsi_14"), digits=1)
    macd = technicals.get("macd_signal") or "n/a"
    if rsi != "n/a" or macd != "n/a":
        out.append(f"- 技术: RSI {rsi}  |  MACD {macd}")
    return out


def _expert_lines(decision: dict) -> list[str]:
    debate = decision.get("agent_debate")
    if not isinstance(debate, dict):
        return []
    labels = {
        "fundamentalist": "基本面",
        "technician": "技术面",
        "sentiment": "情绪/新闻",
    }
    out: list[str] = []
    for key, label in labels.items():
        thesis = debate.get(key)
        if not isinstance(thesis, dict):
            out.append(f"- {label}: 未产出")
            continue
        lean = thesis.get("lean") or "n/a"
        confidence = _fmt_float(thesis.get("confidence"), digits=2)
        rationale = str(thesis.get("rationale") or "").strip()
        point = ""
        points = thesis.get("key_points")
        if isinstance(points, list) and points:
            point = str(points[0])
        detail = point or rationale
        out.append(
            f"- {label}: {lean} ({confidence})"
            + (f" - {_wrap_text(detail, limit=110)}" if detail else "")
        )
    return out


def _fmt_float(value: object, *, digits: int) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_pct(value: object) -> str:
    try:
        return f"{float(value):+.1%}"
    except (TypeError, ValueError):
        return "n/a"


def _wrap_text(text: str, *, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "..."


def _redact_params(params: dict) -> dict:
    return {
        key: ("***" if "token" in str(key).lower() or "secret" in str(key).lower() else value)
        for key, value in params.items()
    }
