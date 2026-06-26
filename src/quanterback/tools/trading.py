from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.interfaces.state import SystemStateService
from quanterback.tools.registry import (
    Tool,
    ToolContext,
    ToolManifest,
    ToolResult,
    ToolSideEffect,
)


StatusRenderer = Callable[[], str]


@dataclass
class SubprocessRunner:
    timeout_s: int = 300

    def run(self, args: list[str], timeout_s: int | None = None) -> ToolResult:
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout_s or self.timeout_s,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, message="命令超时，请检查 logs。")
        if result.returncode != 0:
            return ToolResult(
                ok=False,
                message=(result.stderr[-1000:] if result.stderr else "命令执行失败"),
            )
        return ToolResult(ok=True, message=result.stdout.strip())


def trading_status_tool(render_status: StatusRenderer) -> Tool:
    return Tool(
        manifest=ToolManifest(
            name="trading.status",
            description="Show private trader account/system status.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            side_effect=ToolSideEffect.READ_ONLY,
            scope="trading",
            allowed_interfaces=("trader_bot",),
            requires_setup=("trading_state",),
        ),
        handler=lambda params, context: ToolResult(ok=True, message=render_status()),
    )


def trading_state_tool(
    name: str, mode: str, reason: str, sys_state: SystemStateService,
) -> Tool:
    def _handle(params: dict[str, Any], context: ToolContext) -> ToolResult:
        sys_state.set(mode, reason, context.user_id or "telegram")
        return ToolResult(ok=True, message=f"system state set to {mode}")

    return Tool(
        manifest=ToolManifest(
            name=f"trading.{name}",
            description=f"Set trader system state via {name}.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            side_effect=ToolSideEffect.USER_WRITE,
            scope="trading",
            allowed_interfaces=("trader_bot",),
            requires_confirmation=name in {"halt", "unhalt"},
            requires_setup=("trading_state",),
        ),
        handler=_handle,
    )


def trading_watchlist_tools(store: SqliteStore) -> list[Tool]:
    def list_handler(params: dict[str, Any], context: ToolContext) -> ToolResult:
        entries = store.list_watchlist()
        rows = [f"{e.ticker} ({e.source})" for e in entries]
        return ToolResult(
            ok=True,
            message="\n".join(rows) if rows else "(watchlist is empty)",
            data={"tickers": [e.ticker for e in entries]},
        )

    def add_handler(params: dict[str, Any], context: ToolContext) -> ToolResult:
        ticker = str(params.get("ticker") or "").upper().strip()
        if not ticker:
            return ToolResult(ok=False, message="ticker is required")
        added = store.add_watchlist_ticker(ticker, source="user")
        return ToolResult(ok=True, message=f"{ticker} added" if added else f"{ticker} already exists")

    def remove_handler(params: dict[str, Any], context: ToolContext) -> ToolResult:
        ticker = str(params.get("ticker") or "").upper().strip()
        if not ticker:
            return ToolResult(ok=False, message="ticker is required")
        removed = store.remove_watchlist_ticker(ticker)
        return ToolResult(ok=True, message=f"{ticker} removed" if removed else f"{ticker} not found")

    return [
        Tool(
            manifest=ToolManifest(
                name="trading.watchlist_list",
                description="List the private trader watchlist.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                side_effect=ToolSideEffect.READ_ONLY,
                scope="trading",
                allowed_interfaces=("trader_bot",),
                requires_setup=("trading_state",),
            ),
            handler=list_handler,
        ),
        Tool(
            manifest=ToolManifest(
                name="trading.watchlist_add",
                description="Add one ticker to the private trader watchlist.",
                input_schema={
                    "type": "object",
                    "properties": {"ticker": {"type": "string"}},
                    "required": ["ticker"],
                    "additionalProperties": False,
                },
                side_effect=ToolSideEffect.USER_WRITE,
                scope="trading",
                allowed_interfaces=("trader_bot",),
                requires_setup=("trading_state",),
            ),
            handler=add_handler,
        ),
        Tool(
            manifest=ToolManifest(
                name="trading.watchlist_remove",
                description="Remove one ticker from the private trader watchlist.",
                input_schema={
                    "type": "object",
                    "properties": {"ticker": {"type": "string"}},
                    "required": ["ticker"],
                    "additionalProperties": False,
                },
                side_effect=ToolSideEffect.USER_WRITE,
                scope="trading",
                allowed_interfaces=("trader_bot",),
                requires_setup=("trading_state",),
            ),
            handler=remove_handler,
        ),
    ]


def trading_scan_tools(runner: SubprocessRunner) -> list[Tool]:
    def _tickers(params: dict[str, Any]) -> list[str]:
        raw = params.get("tickers") or []
        if isinstance(raw, str):
            raw = [raw]
        return [str(t).upper() for t in raw if str(t).strip()]

    def scan_handler(params: dict[str, Any], context: ToolContext) -> ToolResult:
        tickers = _tickers(params)
        if not tickers:
            return ToolResult(ok=False, message="ticker is required")
        return runner.run([
            "quanterback", "scan", "--format", "brief",
            "--tickers", ",".join(tickers),
        ])

    def preview_handler(params: dict[str, Any], context: ToolContext) -> ToolResult:
        tickers = _tickers(params)
        args = ["quanterback", "scan", "--format", "brief", "--dry-run"]
        if tickers:
            args.extend(["--tickers", ",".join(tickers)])
            return runner.run(args)
        return runner.run(["quanterback", "rescan", "--format", "brief", "--dry-run"], timeout_s=600)

    def rescan_handler(params: dict[str, Any], context: ToolContext) -> ToolResult:
        return runner.run(["quanterback", "rescan", "--format", "brief"], timeout_s=600)

    return [
        _subprocess_tool("trading.scan_tickers", "Run live trader scan for specific tickers.", True, scan_handler),
        _subprocess_tool("trading.preview_tickers", "Run dry-run preview for ticker(s).", False, preview_handler),
        _subprocess_tool("trading.rescan_watchlist", "Run live trader rescan for full watchlist.", True, rescan_handler),
    ]


def _subprocess_tool(
    name: str,
    description: str,
    requires_confirmation: bool,
    handler: Callable[[dict[str, Any], ToolContext], ToolResult],
) -> Tool:
    return Tool(
        manifest=ToolManifest(
            name=name,
            description=description,
            input_schema={"type": "object", "additionalProperties": True},
            side_effect=ToolSideEffect.USER_WRITE if requires_confirmation else ToolSideEffect.READ_ONLY,
            scope="trading",
            allowed_interfaces=("trader_bot",),
            requires_confirmation=requires_confirmation,
            requires_setup=("trading_state",),
        ),
        handler=handler,
    )


def build_trading_tools(
    *,
    store: SqliteStore,
    sys_state: SystemStateService,
    render_status: StatusRenderer,
    runner: SubprocessRunner | None = None,
) -> list[Tool]:
    runner = runner or SubprocessRunner()
    return [
        trading_status_tool(render_status),
        trading_state_tool("freeze", "frozen", "user-requested via Telegram", sys_state),
        trading_state_tool("unfreeze", "normal", "user-requested via Telegram", sys_state),
        trading_state_tool("halt", "halted", "user-requested via Telegram", sys_state),
        trading_state_tool("unhalt", "normal", "user-requested via Telegram", sys_state),
        *trading_watchlist_tools(store),
        *trading_scan_tools(runner),
    ]

