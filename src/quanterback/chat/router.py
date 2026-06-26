from __future__ import annotations

import re

from quanterback.chat.models import ChatIntent

_TICKER_RE = re.compile(r"\b[A-Z][A-Z0-9.\-]{0,9}\b")


class ResearchChatRouter:
    """Small deterministic router for v1 chat.

    Command-based UX is kept as a shortcut layer, not as a separate command
    implementation. Both slash commands and natural text resolve to tool calls.
    """

    def route(self, text: str) -> ChatIntent:
        raw = text.strip()
        lowered = raw.lower().strip()
        if not raw:
            return ChatIntent(kind="unknown", confidence=0.0)
        if lowered in {"/yes", "yes", "y", "确认", "确定", "执行"}:
            return ChatIntent(kind="confirm")
        if lowered in {"/no", "no", "n", "取消", "算了"}:
            return ChatIntent(kind="cancel")
        if lowered in {"/help", "help", "帮助"}:
            return ChatIntent(kind="help")
        if raw.startswith("/"):
            return self._route_command(raw)
        return self._route_natural(raw)

    def _route_command(self, raw: str) -> ChatIntent:
        tokens = raw.split()
        head = tokens[0].split("@", 1)[0].lower()
        args = tokens[1:]
        if head in {"/analyze", "/ask", "/ticker"} and args:
            return ChatIntent(
                kind="tool",
                tool_name="research.analyze_ticker",
                params={"ticker": args[0].upper()},
            )
        if head in {"/add", "/watch"} and args:
            return ChatIntent(
                kind="tool",
                tool_name="research.watchlist_add",
                params={"ticker": args[0].upper()},
            )
        if head in {"/remove", "/rm", "/unwatch"} and args:
            return ChatIntent(
                kind="tool",
                tool_name="research.watchlist_remove",
                params={"ticker": args[0].upper()},
            )
        if head in {"/watchlist", "/list"}:
            return ChatIntent(kind="tool", tool_name="research.watchlist_list")
        if head in {"/jobs"}:
            return ChatIntent(kind="tool", tool_name="research.list_jobs")
        if head in {"/cancel"} and args:
            return ChatIntent(
                kind="tool",
                tool_name="research.cancel_digest",
                params={"job_id": args[0]},
            )
        if head in {"/digest", "/report"}:
            params = _parse_digest_args(args)
            return ChatIntent(
                kind="tool",
                tool_name="research.schedule_digest",
                params=params,
            )
        return ChatIntent(kind="help", confidence=0.3)

    def _route_natural(self, raw: str) -> ChatIntent:
        lowered = raw.lower()
        ticker = _extract_ticker(raw)
        if ticker and any(k in lowered for k in ("analyze", "analyse", "分析", "看看")):
            return ChatIntent(
                kind="tool",
                tool_name="research.analyze_ticker",
                params={"ticker": ticker},
                confidence=0.75,
            )
        if ticker and any(k in lowered for k in ("add", "watch", "加入", "加到")):
            return ChatIntent(
                kind="tool",
                tool_name="research.watchlist_add",
                params={"ticker": ticker},
                confidence=0.75,
            )
        if ticker and any(k in lowered for k in ("remove", "delete", "删", "移除")):
            return ChatIntent(
                kind="tool",
                tool_name="research.watchlist_remove",
                params={"ticker": ticker},
                confidence=0.75,
            )
        if any(k in lowered for k in ("watchlist", "关注列表", "自选", "列表")):
            return ChatIntent(
                kind="tool", tool_name="research.watchlist_list", confidence=0.7,
            )
        if any(k in lowered for k in ("jobs", "任务", "定时")):
            return ChatIntent(
                kind="tool", tool_name="research.list_jobs", confidence=0.6,
            )
        if any(k in lowered for k in ("digest", "report", "日报", "报告")):
            return ChatIntent(
                kind="tool",
                tool_name="research.schedule_digest",
                params={"schedule_kind": "daily", "schedule_spec": {"text": raw}},
                confidence=0.55,
            )
        return ChatIntent(kind="unknown", confidence=0.0)


def _extract_ticker(text: str) -> str | None:
    for match in _TICKER_RE.finditer(text.upper()):
        token = match.group(0)
        if token in {"I", "A", "THE", "AND", "OR", "ETF"}:
            continue
        return token
    return None


def _parse_digest_args(args: list[str]) -> dict:
    params: dict = {"schedule_kind": "daily", "schedule_spec": {}}
    if args:
        params["schedule_spec"] = {"raw": " ".join(args)}
    return params

