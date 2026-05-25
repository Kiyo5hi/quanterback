from __future__ import annotations

from dataclasses import dataclass, field

from quanterback.adapters.decision.cached_llm_client import CachedLLMClient
from quanterback.interfaces.decision import ChatMessage, ChatResponse


@dataclass
class CountingClient:
    """Underlying client that counts how many real calls happen."""
    canned: str = '{"x":1}'
    n_calls: int = 0
    last_call_kwargs: dict = field(default_factory=dict)

    def chat(self, messages, *, response_schema=None, temperature=0.0):
        self.n_calls += 1
        self.last_call_kwargs = {
            "messages": messages, "schema": response_schema, "temp": temperature,
        }
        return ChatResponse(content=self.canned, model="fake",
                             usage={"input_tokens": 0, "output_tokens": 0})


def test_cache_hits_on_identical_call() -> None:
    inner = CountingClient()
    cached = CachedLLMClient(wrapped=inner)
    msgs = [ChatMessage(role="user", content="hi")]
    cached.chat(msgs)
    cached.chat(msgs)
    cached.chat(msgs)
    assert inner.n_calls == 1
    assert cached.hits == 2
    assert cached.misses == 1


def test_cache_miss_when_messages_differ() -> None:
    inner = CountingClient()
    cached = CachedLLMClient(wrapped=inner)
    cached.chat([ChatMessage(role="user", content="A")])
    cached.chat([ChatMessage(role="user", content="B")])
    assert inner.n_calls == 2
    assert cached.misses == 2
    assert cached.hits == 0


def test_cache_miss_when_temperature_differs() -> None:
    inner = CountingClient()
    cached = CachedLLMClient(wrapped=inner)
    msgs = [ChatMessage(role="user", content="hi")]
    cached.chat(msgs, temperature=0.0)
    cached.chat(msgs, temperature=0.5)
    assert inner.n_calls == 2


def test_cache_miss_when_schema_differs() -> None:
    inner = CountingClient()
    cached = CachedLLMClient(wrapped=inner)
    msgs = [ChatMessage(role="user", content="hi")]
    cached.chat(msgs, response_schema={"a": 1})
    cached.chat(msgs, response_schema={"a": 2})
    assert inner.n_calls == 2


def test_cached_response_content_equals_underlying() -> None:
    inner = CountingClient(canned='{"hello":"world"}')
    cached = CachedLLMClient(wrapped=inner)
    msgs = [ChatMessage(role="user", content="hi")]
    r1 = cached.chat(msgs)
    r2 = cached.chat(msgs)
    assert r1.content == r2.content == '{"hello":"world"}'
