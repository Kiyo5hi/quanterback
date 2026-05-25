from __future__ import annotations

from pathlib import Path

from quanterback.adapters.decision.prompt import (
    DECISION_RESPONSE_SCHEMA,
    render_prompt,
)


def test_render_prompt_inlines_summary_text(tmp_path: Path) -> None:
    tpl = tmp_path / "tpl.md"
    tpl.write_text("SYSTEM\n--SUMMARY--\nEND")
    summary_text = "[AAPL] price 100"
    out = render_prompt(tpl, summary_text)
    assert "SYSTEM" in out
    assert summary_text in out


def test_response_schema_has_required_fields() -> None:
    s = DECISION_RESPONSE_SCHEMA
    assert "action" in s["properties"]
    assert s["required"] == ["action", "ticker", "strategy", "rationale", "confidence"]


def test_response_schema_supports_both_strategies() -> None:
    s = DECISION_RESPONSE_SCHEMA
    assert s["properties"]["strategy"]["enum"] == ["MOMENTUM", "MEAN_REVERSION"]
    # params has a union; check it's not the old single-shape
    assert "oneOf" in s["properties"]["params"]
    assert len(s["properties"]["params"]["oneOf"]) == 3  # momentum, MR, null
