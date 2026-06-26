from __future__ import annotations

from dataclasses import dataclass, field

from quanterback.capabilities.research import ResearchAnalyzer
from quanterback.interfaces.research_store import ResearchStore
from quanterback.tools.registry import Tool, ToolRegistry
from quanterback.tools.research import (
    analyze_ticker_tool,
    cancel_digest_tool,
    list_jobs_tool,
    schedule_digest_tool,
    watchlist_add_tool,
    watchlist_list_tool,
    watchlist_remove_tool,
)
from quanterback.tools.trading import build_trading_tools


CAPABILITY_TOOLS: dict[str, tuple[str, ...]] = {
    "research.analyze_ticker": ("research.analyze_ticker",),
    # User-facing capability bundle. Concrete tools will be added as the
    # public chat surface grows.
    "research.watchlist": (
        "research.watchlist_add",
        "research.watchlist_remove",
        "research.watchlist_list",
    ),
    "research.digest_jobs": (
        "research.schedule_digest",
        "research.cancel_digest",
        "research.list_jobs",
    ),
    "trading.control": (
        "trading.freeze",
        "trading.unfreeze",
        "trading.halt",
        "trading.unhalt",
    ),
    "trading.scan": (
        "trading.scan_tickers",
        "trading.preview_tickers",
        "trading.rescan_watchlist",
    ),
    "trading.status": ("trading.status",),
    "trading.watchlist": (
        "trading.watchlist_add",
        "trading.watchlist_remove",
        "trading.watchlist_list",
    ),
}


@dataclass(frozen=True)
class CapabilitySelection:
    """Deployment-selected capabilities and low-level tool overrides."""

    enabled: tuple[str, ...] = ()
    include_tools: tuple[str, ...] = ()
    exclude_tools: tuple[str, ...] = ()

    @classmethod
    def from_toml(cls, raw: dict | None) -> "CapabilitySelection":
        raw = raw or {}
        capabilities = raw.get("capabilities", {})
        tools = raw.get("tools", {})
        return cls(
            enabled=_as_tuple(capabilities.get("enabled", ())),
            include_tools=_as_tuple(tools.get("include", ())),
            exclude_tools=_as_tuple(tools.get("exclude", ())),
        )

    def resolve_tool_names(
        self,
        *,
        capability_map: dict[str, tuple[str, ...]] | None = None,
    ) -> tuple[str, ...]:
        capability_map = capability_map or CAPABILITY_TOOLS
        resolved: set[str] = set()
        for capability in self.enabled:
            resolved.update(capability_map.get(capability, (capability,)))
        resolved.update(self.include_tools)
        resolved.difference_update(self.exclude_tools)
        return tuple(sorted(resolved))


@dataclass
class CapabilityCatalog:
    """Known concrete tools, filtered by deployment capability selection."""

    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        self.tools[tool.manifest.name] = tool

    def registry_for(self, selection: CapabilitySelection) -> ToolRegistry:
        selected_names = set(selection.resolve_tool_names())
        selected = [
            tool for name, tool in self.tools.items()
            if name in selected_names
        ]
        return ToolRegistry(selected)

    def unknown_tool_names(self, selection: CapabilitySelection) -> tuple[str, ...]:
        selected_names = set(selection.resolve_tool_names())
        return tuple(sorted(selected_names - set(self.tools)))


def build_research_catalog(
    *,
    analyzer: ResearchAnalyzer | None = None,
    store: ResearchStore | None = None,
) -> CapabilityCatalog:
    catalog = CapabilityCatalog()
    if analyzer is not None:
        catalog.register(analyze_ticker_tool(analyzer))
    if store is not None:
        catalog.register(watchlist_add_tool(store))
        catalog.register(watchlist_remove_tool(store))
        catalog.register(watchlist_list_tool(store))
        catalog.register(schedule_digest_tool(store))
        catalog.register(cancel_digest_tool(store))
        catalog.register(list_jobs_tool(store))
    return catalog


def build_trading_catalog(**kwargs: object) -> CapabilityCatalog:
    catalog = CapabilityCatalog()
    for tool in build_trading_tools(**kwargs):  # type: ignore[arg-type]
        catalog.register(tool)
    return catalog


def _as_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    raise TypeError(f"expected string/list/tuple, got {type(value).__name__}")
