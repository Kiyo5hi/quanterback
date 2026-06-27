from __future__ import annotations

from quanterback.chat.intent import LLMIntentResolver
from quanterback.interfaces.decision import ChatMessage, ChatResponse, ChatTool, ChatToolCall
from quanterback.tools.registry import ToolManifest, ToolSideEffect


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.last_messages: list[ChatMessage] = []
        self.calls = 0

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        response_schema: dict | None = None,
        temperature: float = 0.0,
    ) -> ChatResponse:
        self.calls += 1
        self.last_messages = messages
        return ChatResponse(content=self.content, model="fake", usage={})


class FakeToolCallingLLM(FakeLLM):
    def __init__(self, call_name: str, arguments: dict) -> None:
        super().__init__('{"kind":"unknown","tool_name":null,"params":{},"confidence":0.0}')
        self.call_name = call_name
        self.arguments = arguments
        self.last_tools: list[ChatTool] = []

    def chat_tool_call(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ChatTool],
        temperature: float = 0.0,
    ) -> ChatToolCall | None:
        self.last_messages = messages
        self.last_tools = tools
        return ChatToolCall(
            name=self.call_name,
            arguments=self.arguments,
            model="fake",
            usage={},
        )


def _manifest(name: str) -> ToolManifest:
    return ToolManifest(
        name=name,
        description="test tool",
        input_schema={
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
            "additionalProperties": False,
        },
        side_effect=ToolSideEffect.READ_ONLY,
        scope="research",
    )


def test_llm_intent_resolver_accepts_available_tool() -> None:
    resolver = LLMIntentResolver(FakeLLM(
        '{"kind":"tool","tool_name":"research.analyze_ticker",'
        '"params":{"ticker":"NVDA"},"confidence":0.92}'
    ))

    intent = resolver.resolve("帮我看一下 nvda", [_manifest("research.analyze_ticker")])

    assert intent.kind == "tool"
    assert intent.tool_name == "research.analyze_ticker"
    assert intent.params == {"ticker": "NVDA"}


def test_llm_intent_resolver_prefers_function_call() -> None:
    llm = FakeToolCallingLLM("research__analyze_ticker", {"ticker": "NVDA"})
    resolver = LLMIntentResolver(llm)

    intent = resolver.resolve("帮我看一下 nvda", [_manifest("research.analyze_ticker")])

    assert intent.kind == "tool"
    assert intent.tool_name == "research.analyze_ticker"
    assert intent.params == {"ticker": "NVDA"}
    assert [tool.name for tool in llm.last_tools] == ["research__analyze_ticker"]
    assert llm.calls == 0


def test_llm_intent_resolver_rejects_unavailable_tool() -> None:
    resolver = LLMIntentResolver(FakeLLM(
        '{"kind":"tool","tool_name":"trading.scan_tickers",'
        '"params":{"tickers":["NVDA"]},"confidence":0.92}'
    ))

    intent = resolver.resolve("scan nvda", [_manifest("research.analyze_ticker")])

    assert intent.kind == "unknown"
