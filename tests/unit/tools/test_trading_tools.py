from __future__ import annotations

from quanterback.adapters.state.sqlite_system_state import SqliteSystemStateService
from quanterback.adapters.store.sqlite_store import SqliteStore
from quanterback.tools.capabilities import CapabilitySelection, build_trading_catalog
from quanterback.tools.registry import ToolContext
from quanterback.tools.trading import SubprocessRunner, build_trading_tools


class FakeRunner(SubprocessRunner):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[list[str]] = []

    def run(self, args: list[str], timeout_s: int | None = None):
        from quanterback.tools.registry import ToolResult
        self.calls.append(args)
        return ToolResult(ok=True, message="ran")


def test_trading_state_and_watchlist_tools(tmp_path) -> None:
    store = SqliteStore(tmp_path / "q.sqlite")
    sys_state = SqliteSystemStateService(store)
    tools = build_trading_tools(
        store=store,
        sys_state=sys_state,
        render_status=lambda: "status-ok",
        runner=FakeRunner(),
    )
    registry = build_trading_catalog(
        store=store,
        sys_state=sys_state,
        render_status=lambda: "status-ok",
        runner=FakeRunner(),
    ).registry_for(CapabilitySelection(enabled=(
        "trading.control", "trading.status", "trading.watchlist",
    )))
    context = ToolContext(interface="trader_bot", user_id="42", setup=frozenset({"trading_state"}))

    assert {t.manifest.name for t in tools}
    assert registry.execute("trading.freeze", {}, context).ok is True
    assert sys_state.get_current().mode == "frozen"
    assert registry.execute("trading.status", {}, context).message == "status-ok"
    assert registry.execute("trading.watchlist_add", {"ticker": "nvda"}, context).ok is True
    assert registry.execute("trading.watchlist_list", {}, context).data["tickers"] == ["NVDA"]


def test_trading_scan_requires_confirmation_and_preview_does_not(tmp_path) -> None:
    store = SqliteStore(tmp_path / "q.sqlite")
    sys_state = SqliteSystemStateService(store)
    runner = FakeRunner()
    registry = build_trading_catalog(
        store=store, sys_state=sys_state, render_status=lambda: "ok", runner=runner,
    ).registry_for(CapabilitySelection(enabled=("trading.scan",)))
    context = ToolContext(interface="trader_bot", user_id="42", setup=frozenset({"trading_state"}))

    blocked = registry.execute("trading.scan_tickers", {"tickers": ["NVDA"]}, context)
    preview = registry.execute("trading.preview_tickers", {"tickers": ["NVDA"]}, context)
    live = registry.execute(
        "trading.scan_tickers", {"tickers": ["NVDA"]}, context, confirmed=True,
    )

    assert blocked.data["confirmation_required"] is True
    assert preview.ok is True
    assert live.ok is True
    assert runner.calls == [
        ["quanterback", "scan", "--format", "brief", "--dry-run", "--tickers", "NVDA"],
        ["quanterback", "scan", "--format", "brief", "--tickers", "NVDA"],
    ]
