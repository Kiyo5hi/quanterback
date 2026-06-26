from __future__ import annotations

from dataclasses import dataclass, field

from quanterback.tools.registry import Tool, ToolRegistry


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


def _as_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    raise TypeError(f"expected string/list/tuple, got {type(value).__name__}")

