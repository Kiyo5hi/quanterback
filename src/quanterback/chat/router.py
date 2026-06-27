from __future__ import annotations

import re
from dataclasses import dataclass

from quanterback.chat.models import ChatIntent

_TICKER_RE = re.compile(r"\$?([A-Za-z][A-Za-z0-9.\-]{0,9})")

_TICKER_STOPWORDS = {
    "A",
    "ADD",
    "ANALYSE",
    "ANALYZE",
    "AND",
    "ASK",
    "CANCEL",
    "DELETE",
    "DIGEST",
    "ETF",
    "FROM",
    "HELP",
    "I",
    "IN",
    "JOB",
    "JOBS",
    "LIST",
    "ME",
    "MY",
    "NO",
    "OR",
    "PLEASE",
    "REMOVE",
    "REPORT",
    "SHOW",
    "THE",
    "TICKER",
    "TO",
    "UNWATCH",
    "WATCH",
    "WATCHLIST",
    "YES",
}


@dataclass
class ResearchChatRouter:
    """Deterministic router for commands and confirmation tokens.

    Slash commands are kept as shortcuts. Free-form natural language is routed
    by ResearchChatService via the LLM intent resolver; this class only exposes
    a conservative fallback for deployments that cannot call an LLM.
    """

    enable_trading_commands: bool = False

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
        return ChatIntent(kind="unknown", confidence=0.0)

    def route_natural_fallback(self, text: str) -> ChatIntent:
        return self._route_natural(text)

    def _route_command(self, raw: str) -> ChatIntent:
        tokens = raw.split()
        head = tokens[0].split("@", 1)[0].lower()
        args = tokens[1:]
        if self.enable_trading_commands:
            routed = self._route_trading_command(head, args)
            if routed is not None:
                return routed
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

    def _route_trading_command(self, head: str, args: list[str]) -> ChatIntent | None:
        if head in {"/status"}:
            return ChatIntent(kind="tool", tool_name="trading.status")
        if head in {"/freeze", "/unfreeze", "/halt", "/unhalt"}:
            return ChatIntent(
                kind="tool",
                tool_name=f"trading.{head[1:]}",
                params={},
            )
        if head in {"/scan"} and args:
            return ChatIntent(
                kind="tool",
                tool_name="trading.scan_tickers",
                params={"tickers": [a.upper() for a in args]},
            )
        if head in {"/preview"}:
            return ChatIntent(
                kind="tool",
                tool_name="trading.preview_tickers",
                params={"tickers": [a.upper() for a in args]},
            )
        if head in {"/rescan"}:
            return ChatIntent(kind="tool", tool_name="trading.rescan_watchlist")
        if head in {"/watchlist"}:
            if not args or args[0].lower() == "list":
                return ChatIntent(kind="tool", tool_name="trading.watchlist_list")
            if args[0].lower() == "add" and len(args) >= 2:
                return ChatIntent(
                    kind="tool",
                    tool_name="trading.watchlist_add",
                    params={"ticker": args[1].upper()},
                )
            if args[0].lower() == "remove" and len(args) >= 2:
                return ChatIntent(
                    kind="tool",
                    tool_name="trading.watchlist_remove",
                    params={"ticker": args[1].upper()},
                )
        if head in {"/add"} and args:
            return ChatIntent(
                kind="tool",
                tool_name="trading.watchlist_add",
                params={"ticker": args[0].upper()},
            )
        if head in {"/remove"} and args:
            return ChatIntent(
                kind="tool",
                tool_name="trading.watchlist_remove",
                params={"ticker": args[0].upper()},
            )
        return None

    def _route_natural(self, raw: str) -> ChatIntent:
        lowered = raw.lower()
        ticker = _extract_ticker(raw)
        if self.enable_trading_commands:
            routed = self._route_trading_natural(raw, lowered, ticker)
            if routed is not None:
                return routed
        if ticker and _has_any(
            lowered,
            (
                "analyze", "analyse", "research", "look at", "check",
                "分析", "研究", "看看", "看下", "看一下", "查一下", "查下",
                "怎么样", "如何", "能买吗", "值得买", "走势", "前景",
            ),
        ):
            return ChatIntent(
                kind="tool",
                tool_name="research.analyze_ticker",
                params={"ticker": ticker},
                confidence=0.75,
            )
        if ticker and _has_any(
            lowered,
            ("add", "watch", "follow", "track", "加入", "加到", "加进", "关注", "盯一下"),
        ):
            return ChatIntent(
                kind="tool",
                tool_name="research.watchlist_add",
                params={"ticker": ticker},
                confidence=0.75,
            )
        if ticker and _has_any(
            lowered,
            ("remove", "delete", "unwatch", "删", "删除", "移除", "去掉", "取消关注"),
        ):
            return ChatIntent(
                kind="tool",
                tool_name="research.watchlist_remove",
                params={"ticker": ticker},
                confidence=0.75,
            )
        if _has_any(lowered, ("watchlist", "关注列表", "自选", "列表", "我的票")):
            return ChatIntent(
                kind="tool", tool_name="research.watchlist_list", confidence=0.7,
            )
        if _has_any(lowered, ("jobs", "任务", "定时")) and _has_any(
            lowered, ("list", "show", "看", "列", "哪些", "有什么", "查询"),
        ):
            return ChatIntent(
                kind="tool", tool_name="research.list_jobs", confidence=0.6,
            )
        if _has_any(lowered, ("digest", "report", "日报", "报告", "简报", "复盘")):
            return ChatIntent(
                kind="tool",
                tool_name="research.schedule_digest",
                params={"schedule_kind": "daily", "schedule_spec": {"text": raw}},
                confidence=0.55,
            )
        return ChatIntent(kind="unknown", confidence=0.0)

    def _route_trading_natural(
        self, raw: str, lowered: str, ticker: str | None,
    ) -> ChatIntent | None:
        if _has_any(lowered, ("status", "状态", "情况")) and not ticker:
            return ChatIntent(kind="tool", tool_name="trading.status", confidence=0.7)
        if _has_any(lowered, ("unfreeze", "解冻", "恢复交易")):
            return ChatIntent(kind="tool", tool_name="trading.unfreeze", confidence=0.7)
        if _has_any(lowered, ("freeze", "冻结", "暂停交易")):
            return ChatIntent(kind="tool", tool_name="trading.freeze", confidence=0.7)
        if _has_any(lowered, ("unhalt", "解除熔断", "解除停止")):
            return ChatIntent(kind="tool", tool_name="trading.unhalt", confidence=0.7)
        if _has_any(lowered, ("halt", "熔断", "停止交易")):
            return ChatIntent(kind="tool", tool_name="trading.halt", confidence=0.7)
        if ticker and _has_any(lowered, ("preview", "预览", "dry run", "dry-run", "试跑")):
            return ChatIntent(
                kind="tool",
                tool_name="trading.preview_tickers",
                params={"tickers": [ticker]},
                confidence=0.75,
            )
        if ticker and _has_any(lowered, ("scan", "扫描", "跑一下", "跑下")):
            return ChatIntent(
                kind="tool",
                tool_name="trading.scan_tickers",
                params={"tickers": [ticker]},
                confidence=0.7,
            )
        if _has_any(lowered, ("rescan", "重新扫描", "全量扫描", "扫一遍")):
            return ChatIntent(kind="tool", tool_name="trading.rescan_watchlist", confidence=0.7)
        return None


def _extract_ticker(text: str) -> str | None:
    for match in _TICKER_RE.finditer(text):
        token = match.group(1).upper()
        if token in _TICKER_STOPWORDS:
            continue
        return token
    return None


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _parse_digest_args(args: list[str]) -> dict:
    params: dict = {"schedule_kind": "daily", "schedule_spec": {}}
    if args:
        params["schedule_spec"] = {"raw": " ".join(args)}
    return params
