from __future__ import annotations

from quanterback.tools.registry import (
    Tool,
    ToolContext,
    ToolManifest,
    ToolRegistry,
    ToolResult,
    ToolSideEffect,
)


def test_registry_execute_enforces_confirmation() -> None:
    calls = {"n": 0}

    def handler(params, context):
        calls["n"] += 1
        return ToolResult(ok=True, message="done")

    registry = ToolRegistry([
        Tool(
            manifest=ToolManifest(
                name="research.write",
                description="write test",
                input_schema={"type": "object"},
                side_effect=ToolSideEffect.USER_WRITE,
                scope="research",
                requires_confirmation=True,
            ),
            handler=handler,
        ),
    ])

    blocked = registry.execute(
        "research.write", {}, ToolContext(interface="research_chat"),
    )
    ok = registry.execute(
        "research.write", {}, ToolContext(interface="research_chat"),
        confirmed=True,
    )

    assert blocked.ok is False
    assert blocked.data["confirmation_required"] is True
    assert calls["n"] == 1
    assert ok.ok is True

