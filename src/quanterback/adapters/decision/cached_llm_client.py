from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from quanterback.interfaces.decision import ChatMessage, ChatResponse, LLMClient


@dataclass
class CachedLLMClient:
    """Decorator wrapping any LLMClient with an in-memory response cache.

    Cache key = sha256(json.dumps({messages, schema, temperature})).
    Lifetime is the wrapper instance; cache dies with the process.
    """

    wrapped: LLMClient
    _cache: dict[str, ChatResponse] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        response_schema: dict | None = None,
        temperature: float = 0.0,
    ) -> ChatResponse:
        key = self._key(messages, response_schema, temperature)
        cached = self._cache.get(key)
        if cached is not None:
            self.hits += 1
            return cached
        self.misses += 1
        resp = self.wrapped.chat(messages, response_schema=response_schema, temperature=temperature)
        self._cache[key] = resp
        return resp

    @staticmethod
    def _key(
        messages: list[ChatMessage],
        response_schema: dict | None,
        temperature: float,
    ) -> str:
        payload = {
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "schema": response_schema,
            "temperature": temperature,
        }
        s = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(s.encode("utf-8")).hexdigest()
