from __future__ import annotations

import pytest

from quanterback.adapters.decision.claude_client import ClaudeClient
from quanterback.interfaces.decision import ChatMessage


def test_claude_client_calls_sdk_with_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            class Resp:
                content = [type("B", (), {"text": '{"ok":true}'})]
                usage = type("U", (), {"input_tokens": 10, "output_tokens": 5})
                model = "claude-sonnet-4-6"
            return Resp()

    class FakeAnthropic:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.messages = FakeMessages()

    monkeypatch.setattr(
        "quanterback.adapters.decision.claude_client.Anthropic",
        FakeAnthropic,
    )

    client = ClaudeClient(api_key="sk", model="claude-sonnet-4-6")
    msgs = [
        ChatMessage(role="system", content="be precise"),
        ChatMessage(role="user", content="data here"),
    ]
    resp = client.chat(msgs, response_schema={"foo": "bar"}, temperature=0.0)
    assert resp.content == '{"ok":true}'
    assert resp.usage["input_tokens"] == 10
    assert captured["model"] == "claude-sonnet-4-6"
    assert "system" in captured
    assert captured["temperature"] == 0.0


def test_claude_client_passes_thinking_when_effort_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            class Resp:
                content = [type("B", (), {"text": '{"ok":true}'})]
                usage = type("U", (), {"input_tokens": 10, "output_tokens": 5})
                model = "claude-sonnet-4-6"
            return Resp()

    class FakeAnthropic:
        def __init__(self, api_key: str) -> None:
            self.messages = FakeMessages()

    monkeypatch.setattr(
        "quanterback.adapters.decision.claude_client.Anthropic",
        FakeAnthropic,
    )

    client = ClaudeClient(
        api_key="sk", model="claude-sonnet-4-6", thinking_effort="medium",
    )
    client.chat([ChatMessage(role="user", content="hi")], temperature=0.3)
    assert captured["thinking"] == {"type": "enabled", "budget_tokens": 4096}
    # Temperature forced to 1.0 when thinking is on
    assert captured["temperature"] == 1.0


def test_claude_client_omits_thinking_when_effort_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}
    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            class Resp:
                content = [type("B", (), {"text": "{}"})]
                usage = type("U", (), {"input_tokens": 0, "output_tokens": 0})
                model = "x"
            return Resp()
    class FakeAnthropic:
        def __init__(self, api_key: str) -> None:
            self.messages = FakeMessages()
    monkeypatch.setattr(
        "quanterback.adapters.decision.claude_client.Anthropic",
        FakeAnthropic,
    )
    client = ClaudeClient(api_key="sk", model="m")  # default thinking_effort="off"
    client.chat([ChatMessage(role="user", content="hi")], temperature=0.0)
    assert "thinking" not in captured
    assert captured["temperature"] == 0.0
