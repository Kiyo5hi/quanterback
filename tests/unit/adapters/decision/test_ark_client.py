from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError

from quanterback.adapters.decision.ark_client import TOOL_NAME, ArkClient
from quanterback.interfaces.decision import ChatMessage


def _make_fake_openai(monkeypatch, completion_factory):
    """completion_factory(**kwargs) -> response or raises"""
    calls: list[dict] = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(dict(kwargs))
            return completion_factory(**kwargs)

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, api_key: str, base_url: str) -> None:
            self.chat = FakeChat()

    monkeypatch.setattr(
        "quanterback.adapters.decision.ark_client.OpenAI",
        FakeOpenAI,
    )
    return calls


def _bad_request(reason: str) -> BadRequestError:
    req = httpx.Request("POST", "https://ark/api")
    resp = httpx.Response(400, request=req, json={"error": {"message": reason}})
    return BadRequestError(message=reason, response=resp, body={"error": "x"})


def _tool_call_resp(args_json: str):
    return SimpleNamespace(
        model="m",
        choices=[SimpleNamespace(message=SimpleNamespace(
            tool_calls=[SimpleNamespace(
                function=SimpleNamespace(arguments=args_json),
            )],
            content=None,
        ))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def _content_resp(text: str):
    return SimpleNamespace(
        model="m",
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=text, tool_calls=None,
        ))],
        usage=SimpleNamespace(prompt_tokens=8, completion_tokens=3),
    )


def test_no_schema_makes_plain_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _make_fake_openai(monkeypatch, lambda **kw: _content_resp("hello"))
    client = ArkClient(api_key="k", model="m")
    resp = client.chat([ChatMessage(role="user", content="hi")])
    assert resp.content == "hello"
    assert len(calls) == 1
    assert "tools" not in calls[0]
    assert "response_format" not in calls[0]


def test_tier1_tool_call_used_when_schema_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def factory(**kw):
        assert "tools" in kw
        assert kw["tool_choice"]["function"]["name"] == TOOL_NAME
        return _tool_call_resp('{"action":"PASS"}')

    calls = _make_fake_openai(monkeypatch, factory)
    client = ArkClient(api_key="k", model="m")
    resp = client.chat(
        [ChatMessage(role="user", content="hi")],
        response_schema={"foo": "bar"},
    )
    assert resp.content == '{"action":"PASS"}'
    assert client._use_tools is True
    assert len(calls) == 1


def test_tier1_to_tier2_when_tools_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def factory(**kw):
        if "tools" in kw:
            raise _bad_request("tools parameter not supported by this model")
        if "response_format" in kw:
            return _content_resp('{"action":"PASS"}')
        raise AssertionError("unexpected branch")

    _make_fake_openai(monkeypatch, factory)
    client = ArkClient(api_key="k", model="m")
    resp = client.chat(
        [ChatMessage(role="user", content="hi")],
        response_schema={"foo": "bar"},
    )
    assert resp.content == '{"action":"PASS"}'
    assert client._use_tools is False
    assert client._use_response_format is True


def test_tier2_to_tier3_when_response_format_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def factory(**kw):
        if "tools" in kw:
            raise _bad_request("tools parameter not supported")
        if "response_format" in kw:
            raise _bad_request("response_format json_object not supported")
        return _content_resp('plain text reply')

    _make_fake_openai(monkeypatch, factory)
    client = ArkClient(api_key="k", model="m")
    resp = client.chat(
        [ChatMessage(role="user", content="hi")],
        response_schema={"foo": "bar"},
    )
    assert resp.content == "plain text reply"
    assert client._use_tools is False
    assert client._use_response_format is False


def test_subsequent_calls_skip_known_failed_tiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After first call learns tools fail and response_format works,
    subsequent calls go straight to response_format (no retry of tools).
    """
    call_log: list[str] = []
    def factory(**kw):
        if "tools" in kw:
            call_log.append("tools")
            raise _bad_request("tools not supported")
        if "response_format" in kw:
            call_log.append("response_format")
            return _content_resp('{"ok":1}')
        call_log.append("plain")
        return _content_resp("plain")

    _make_fake_openai(monkeypatch, factory)
    client = ArkClient(api_key="k", model="m")
    client.chat([ChatMessage(role="user", content="a")],
                response_schema={"foo": "bar"})
    # First call: tools rejected, then response_format succeeded
    assert call_log == ["tools", "response_format"]

    # Second call: should skip tools entirely, go straight to response_format
    call_log.clear()
    client.chat([ChatMessage(role="user", content="b")],
                response_schema={"foo": "bar"})
    assert call_log == ["response_format"]


def test_non_capability_400_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 400 that's not about tools/response_format must surface to caller."""
    def factory(**kw):
        raise _bad_request("invalid temperature value")

    _make_fake_openai(monkeypatch, factory)
    client = ArkClient(api_key="k", model="m")
    with pytest.raises(BadRequestError, match="invalid temperature"):
        client.chat([ChatMessage(role="user", content="hi")],
                    response_schema={"foo": "bar"})


def test_tool_call_no_tool_calls_falls_back_to_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Some models respond with content even when tool_choice forces a tool.
    The client should use message.content as last resort.
    """
    def factory(**kw):
        return SimpleNamespace(
            model="m",
            choices=[SimpleNamespace(message=SimpleNamespace(
                tool_calls=None,
                content='{"fallback":true}',
            ))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    _make_fake_openai(monkeypatch, factory)
    client = ArkClient(api_key="k", model="m")
    resp = client.chat([ChatMessage(role="user", content="hi")],
                       response_schema={"foo": "bar"})
    assert resp.content == '{"fallback":true}'


def test_ark_client_includes_extra_body_thinking_when_effort_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict] = []
    def factory(**kw):
        captured.append(kw)
        return _content_resp("hello")
    calls = _make_fake_openai(monkeypatch, factory)
    client = ArkClient(api_key="k", model="m", thinking_effort="medium")
    client.chat([ChatMessage(role="user", content="hi")])
    assert calls[0].get("extra_body") == {"thinking": {"type": "enabled"}}


def test_ark_client_omits_extra_body_when_effort_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict] = []
    def factory(**kw):
        captured.append(kw)
        return _content_resp("hello")
    calls = _make_fake_openai(monkeypatch, factory)
    client = ArkClient(api_key="k", model="m")  # default off
    client.chat([ChatMessage(role="user", content="hi")])
    assert "extra_body" not in calls[0]
