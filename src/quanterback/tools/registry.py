from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolSideEffect(str, Enum):
    READ_ONLY = "read_only"
    USER_WRITE = "user_write"
    INFRA_WRITE = "infra_write"
    BROKER_WRITE = "broker_write"


class ToolManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    input_schema: dict[str, Any]
    side_effect: ToolSideEffect
    scope: str
    allowed_interfaces: tuple[str, ...] = ("research_chat",)
    requires_confirmation: bool = False
    requires_setup: tuple[str, ...] = ()
    default_enabled: bool = True


class ToolContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    interface: str
    user_id: str | None = None
    chat_id: str | None = None
    message_id: int = 0
    language: str = "zh"
    timezone: str = "UTC"
    setup: frozenset[str] = Field(default_factory=frozenset)


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


ToolHandler = Callable[[dict[str, Any], ToolContext], ToolResult]


class Tool(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    manifest: ToolManifest
    handler: ToolHandler

    def execute(self, params: dict[str, Any], context: ToolContext) -> ToolResult:
        if context.interface not in self.manifest.allowed_interfaces:
            return ToolResult(
                ok=False,
                message=(
                    f"tool {self.manifest.name} is not available for "
                    f"interface {context.interface}"
                ),
            )
        missing_setup = [
            key for key in self.manifest.requires_setup
            if key not in context.setup
        ]
        if missing_setup:
            return ToolResult(
                ok=False,
                message=(
                    f"tool {self.manifest.name} requires setup: "
                    f"{', '.join(missing_setup)}"
                ),
            )
        return self.handler(params, context)


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        self._tools[tool.manifest.name] = tool

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def execute(
        self,
        name: str,
        params: dict[str, Any],
        context: ToolContext,
        *,
        confirmed: bool = False,
    ) -> ToolResult:
        tool = self.get(name)
        if tool.manifest.requires_confirmation and not confirmed:
            return ToolResult(
                ok=False,
                message=f"tool {name} requires confirmation",
                data={
                    "confirmation_required": True,
                    "tool": name,
                    "params": params,
                    "side_effect": tool.manifest.side_effect.value,
                },
            )
        return tool.execute(params, context)

    def available_for(self, context: ToolContext) -> list[ToolManifest]:
        out: list[ToolManifest] = []
        for tool in self._tools.values():
            manifest = tool.manifest
            if not manifest.default_enabled:
                continue
            if context.interface not in manifest.allowed_interfaces:
                continue
            if any(key not in context.setup for key in manifest.requires_setup):
                continue
            out.append(manifest)
        return sorted(out, key=lambda m: m.name)
