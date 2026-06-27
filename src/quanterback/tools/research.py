from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timezone
from typing import Any

from quanterback.capabilities.research import ResearchAnalyzer
from quanterback.domain.market import MarketDataQualityError
from quanterback.domain.research import ResearchAuditEvent
from quanterback.interfaces.research_store import ResearchStore
from quanterback.tools.registry import (
    Tool,
    ToolContext,
    ToolManifest,
    ToolResult,
    ToolSideEffect,
)

log = logging.getLogger(__name__)
_ANALYZE_LOCK = threading.Lock()

_TICKER_ALIASES = {
    "NVIDIA": "NVDA",
    "NVIDIA CORP": "NVDA",
    "NVIDIA CORPORATION": "NVDA",
    "NVDA.O": "NVDA",
    "SOX": "SOXX",
    "PHLX SOX": "SOXX",
    "PHILADELPHIA SEMICONDUCTOR INDEX": "SOXX",
}


def _context_user_id(context: ToolContext) -> int | None:
    if context.user_id is None:
        return None
    try:
        return int(context.user_id)
    except ValueError:
        return None


def _require_user_id(context: ToolContext) -> tuple[int | None, ToolResult | None]:
    user_id = _context_user_id(context)
    if user_id is None:
        return None, ToolResult(ok=False, message="authenticated user context is required")
    return user_id, None


def _audit(
    store: ResearchStore,
    context: ToolContext,
    *,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    ticker: str | None = None,
    request: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    user_id = _context_user_id(context)
    store.research_insert_audit_event(
        ResearchAuditEvent(
            occurred_at=datetime.now(tz=timezone.utc),
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            ticker=ticker,
            request_json=json.dumps(request or {}, sort_keys=True),
            result_json=json.dumps(result or {}, sort_keys=True),
        ),
    )


def analyze_ticker_tool(analyzer: ResearchAnalyzer) -> Tool:
    def _handle(params: dict[str, Any], context: ToolContext) -> ToolResult:
        ticker = _canonical_ticker(str(params.get("ticker") or ""))
        if not ticker:
            return ToolResult(ok=False, message="ticker is required")
        log.info("Research analyze started ticker=%s user=%s", ticker, context.user_id)
        try:
            log.info("Research analyze waiting for slot ticker=%s", ticker)
            with _ANALYZE_LOCK:
                log.info("Research analyze acquired slot ticker=%s", ticker)
                result = _analyze_with_timeout(analyzer, ticker)
        except MarketDataQualityError as exc:
            log.info("Research analyze rejected ticker=%s reason=market_data_quality", ticker)
            return ToolResult(
                ok=False,
                message=(
                    f"我没法可靠分析 {ticker}：行情源没有足够可用价格数据。"
                    "可能是 ticker 不对、数据源暂时缺数据，或这个标的不适合当前分析流程。"
                ),
                data={"ticker": ticker, "error": str(exc), "error_type": "market_data_quality"},
            )
        except TimeoutError:
            log.warning("Research analyze timed out ticker=%s", ticker)
            return ToolResult(
                ok=False,
                message=(
                    f"我已经拿到 {ticker} 的行情摘要，但多专家模型分析没有在"
                    "本次等待窗口内完成。这不是 ticker 不能分析，通常是模型服务"
                    "响应过慢。请稍后重试；当前部署会串行处理研究请求，避免并发"
                    "把专家分析打爆。"
                ),
                data={"ticker": ticker, "error_type": "timeout"},
            )
        decision = result.decision
        summary = result.summary
        log.info(
            "Research analyze completed ticker=%s action=%s confidence=%.2f",
            ticker, decision.action, decision.confidence,
        )
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


def _canonical_ticker(raw: str) -> str:
    ticker = raw.strip().upper()
    ticker = ticker.replace("$", "")
    ticker = _TICKER_ALIASES.get(ticker, ticker)
    return ticker


def _analyze_with_timeout(analyzer: ResearchAnalyzer, ticker: str, timeout_s: float = 420.0):
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(analyzer.analyze_ticker, ticker)
        return future.result(timeout=timeout_s)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def watchlist_add_tool(store: ResearchStore) -> Tool:
    def _handle(params: dict[str, Any], context: ToolContext) -> ToolResult:
        user_id, error = _require_user_id(context)
        if error is not None:
            return error
        assert user_id is not None
        ticker = str(params.get("ticker") or "").strip().upper()
        if not ticker:
            return ToolResult(ok=False, message="ticker is required")
        notes = str(params.get("notes") or "")
        added = store.research_add_watchlist_item(user_id, ticker, notes=notes)
        _audit(
            store, context, action="research.watchlist.add",
            entity_type="research_watchlist_item", ticker=ticker,
            request={"ticker": ticker, "notes": notes},
            result={"added": added},
        )
        return ToolResult(
            ok=True,
            message=f"{ticker} added to watchlist" if added else f"{ticker} already in watchlist",
            data={"ticker": ticker, "added": added},
        )

    return Tool(
        manifest=ToolManifest(
            name="research.watchlist_add",
            description="Add one ticker to the authenticated user's research watchlist.",
            input_schema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["ticker"],
                "additionalProperties": False,
            },
            side_effect=ToolSideEffect.USER_WRITE,
            scope="research",
            allowed_interfaces=("research_chat", "agent", "cli"),
            requires_confirmation=False,
            requires_setup=("research_store",),
        ),
        handler=_handle,
    )


def watchlist_remove_tool(store: ResearchStore) -> Tool:
    def _handle(params: dict[str, Any], context: ToolContext) -> ToolResult:
        user_id, error = _require_user_id(context)
        if error is not None:
            return error
        assert user_id is not None
        ticker = str(params.get("ticker") or "").strip().upper()
        if not ticker:
            return ToolResult(ok=False, message="ticker is required")
        removed = store.research_remove_watchlist_item(user_id, ticker)
        _audit(
            store, context, action="research.watchlist.remove",
            entity_type="research_watchlist_item", ticker=ticker,
            request={"ticker": ticker}, result={"removed": removed},
        )
        return ToolResult(
            ok=True,
            message=f"{ticker} removed from watchlist" if removed else f"{ticker} was not in watchlist",
            data={"ticker": ticker, "removed": removed},
        )

    return Tool(
        manifest=ToolManifest(
            name="research.watchlist_remove",
            description="Remove one ticker from the authenticated user's research watchlist.",
            input_schema={
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
                "additionalProperties": False,
            },
            side_effect=ToolSideEffect.USER_WRITE,
            scope="research",
            allowed_interfaces=("research_chat", "agent", "cli"),
            requires_confirmation=False,
            requires_setup=("research_store",),
        ),
        handler=_handle,
    )


def watchlist_list_tool(store: ResearchStore) -> Tool:
    def _handle(params: dict[str, Any], context: ToolContext) -> ToolResult:
        user_id, error = _require_user_id(context)
        if error is not None:
            return error
        assert user_id is not None
        items = store.research_list_watchlist_items(user_id)
        tickers = [item.ticker for item in items]
        return ToolResult(
            ok=True,
            message=", ".join(tickers) if tickers else "watchlist is empty",
            data={
                "tickers": tickers,
                "items": [item.model_dump(mode="json") for item in items],
            },
        )

    return Tool(
        manifest=ToolManifest(
            name="research.watchlist_list",
            description="List the authenticated user's research watchlist.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            side_effect=ToolSideEffect.READ_ONLY,
            scope="research",
            allowed_interfaces=("research_chat", "agent", "cli"),
            requires_setup=("research_store",),
        ),
        handler=_handle,
    )


def schedule_digest_tool(store: ResearchStore) -> Tool:
    def _handle(params: dict[str, Any], context: ToolContext) -> ToolResult:
        user_id, error = _require_user_id(context)
        if error is not None:
            return error
        assert user_id is not None
        schedule_kind = str(params.get("schedule_kind") or "daily")
        schedule_spec_raw = params.get("schedule_spec") or {}
        schedule_spec = (
            schedule_spec_raw
            if isinstance(schedule_spec_raw, str)
            else json.dumps(schedule_spec_raw, sort_keys=True)
        )
        timezone_name = str(params.get("timezone") or context.timezone or "UTC")
        delivery_channel = str(params.get("delivery_channel") or "telegram")
        delivery_target = str(params.get("delivery_target") or context.user_id or "")
        next_run_at = _parse_optional_datetime(params.get("next_run_at"))
        job_id = store.research_create_scheduled_job(
            user_id=user_id,
            job_type="digest",
            schedule_kind=schedule_kind,
            schedule_spec=schedule_spec,
            timezone_name=timezone_name,
            delivery_channel=delivery_channel,
            delivery_target=delivery_target,
            next_run_at=next_run_at,
        )
        _audit(
            store, context, action="research.digest.schedule",
            entity_type="research_scheduled_job", entity_id=str(job_id),
            request={
                "schedule_kind": schedule_kind,
                "schedule_spec": schedule_spec,
                "timezone": timezone_name,
                "delivery_channel": delivery_channel,
            },
            result={"job_id": job_id},
        )
        return ToolResult(
            ok=True,
            message=f"digest job scheduled: {job_id}",
            data={"job_id": job_id},
        )

    return Tool(
        manifest=ToolManifest(
            name="research.schedule_digest",
            description="Schedule a digest job for the authenticated user's watchlist.",
            input_schema={
                "type": "object",
                "properties": {
                    "schedule_kind": {"type": "string", "enum": ["daily", "weekly", "cron"]},
                    "schedule_spec": {"type": ["object", "string"]},
                    "timezone": {"type": "string"},
                    "delivery_channel": {"type": "string"},
                    "delivery_target": {"type": "string"},
                    "next_run_at": {"type": "string"},
                },
                "additionalProperties": False,
            },
            side_effect=ToolSideEffect.USER_WRITE,
            scope="research",
            allowed_interfaces=("research_chat", "agent", "cli"),
            requires_confirmation=True,
            requires_setup=("research_store",),
        ),
        handler=_handle,
    )


def cancel_digest_tool(store: ResearchStore) -> Tool:
    def _handle(params: dict[str, Any], context: ToolContext) -> ToolResult:
        user_id, error = _require_user_id(context)
        if error is not None:
            return error
        assert user_id is not None
        try:
            job_id = int(params.get("job_id"))
        except (TypeError, ValueError):
            return ToolResult(ok=False, message="job_id is required")
        cancelled = store.research_cancel_scheduled_job(user_id, job_id)
        _audit(
            store, context, action="research.digest.cancel",
            entity_type="research_scheduled_job", entity_id=str(job_id),
            request={"job_id": job_id}, result={"cancelled": cancelled},
        )
        return ToolResult(
            ok=True,
            message=f"digest job cancelled: {job_id}" if cancelled else f"digest job not found: {job_id}",
            data={"job_id": job_id, "cancelled": cancelled},
        )

    return Tool(
        manifest=ToolManifest(
            name="research.cancel_digest",
            description="Cancel one of the authenticated user's digest jobs.",
            input_schema={
                "type": "object",
                "properties": {"job_id": {"type": "integer"}},
                "required": ["job_id"],
                "additionalProperties": False,
            },
            side_effect=ToolSideEffect.USER_WRITE,
            scope="research",
            allowed_interfaces=("research_chat", "agent", "cli"),
            requires_confirmation=False,
            requires_setup=("research_store",),
        ),
        handler=_handle,
    )


def list_jobs_tool(store: ResearchStore) -> Tool:
    def _handle(params: dict[str, Any], context: ToolContext) -> ToolResult:
        user_id, error = _require_user_id(context)
        if error is not None:
            return error
        assert user_id is not None
        enabled_only = bool(params.get("enabled_only", True))
        jobs = store.research_list_scheduled_jobs(user_id, enabled_only=enabled_only)
        return ToolResult(
            ok=True,
            message=f"{len(jobs)} scheduled job(s)",
            data={
                "jobs": [job.model_dump(mode="json") for job in jobs],
                "count": len(jobs),
            },
        )

    return Tool(
        manifest=ToolManifest(
            name="research.list_jobs",
            description="List the authenticated user's scheduled research jobs.",
            input_schema={
                "type": "object",
                "properties": {"enabled_only": {"type": "boolean"}},
                "additionalProperties": False,
            },
            side_effect=ToolSideEffect.READ_ONLY,
            scope="research",
            allowed_interfaces=("research_chat", "agent", "cli"),
            requires_setup=("research_store",),
        ),
        handler=_handle,
    )


def _parse_optional_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    raise ValueError("next_run_at must be an ISO datetime string")
