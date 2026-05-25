from __future__ import annotations

import pytest

from quanterback.adapters.control.telegram_commands import (
    COMMANDS,
    register_commands,
)


def test_scan_command_in_commands_list() -> None:
    cmd_names = [c for c, _, _ in COMMANDS]
    assert "scan" in cmd_names


def test_commands_have_both_languages() -> None:
    for cmd, desc_en, desc_zh in COMMANDS:
        assert cmd  # non-empty
        assert desc_en  # non-empty
        assert desc_zh  # non-empty
        assert cmd == cmd.lower()  # lowercase
        # Chinese description should contain a non-ASCII (CJK) character
        assert any(ord(c) > 127 for c in desc_zh)


def test_register_commands_posts_default_and_zh(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_post(url: str, json: dict, timeout: float) -> object:
        calls.append({"url": url, "payload": json})
        class R:
            status_code = 200
            text = "ok"
        return R()

    monkeypatch.setattr(
        "quanterback.adapters.control.telegram_commands.requests.post",
        fake_post,
    )
    register_commands(token="t-test")
    assert len(calls) == 2
    # First call: no language_code (= default)
    assert "language_code" not in calls[0]["payload"]
    # Second call: language_code = zh
    assert calls[1]["payload"].get("language_code") == "zh"
    # Endpoint should mention setMyCommands
    assert "setMyCommands" in calls[0]["url"]


def test_register_commands_does_not_raise_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, json: dict, timeout: float) -> object:
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "quanterback.adapters.control.telegram_commands.requests.post",
        fake_post,
    )
    # Must not raise
    register_commands(token="t")
