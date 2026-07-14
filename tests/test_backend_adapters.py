from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from yagami.backends.anthropic import ClaudeBackend
from yagami.backends.base import BackendOptions, Capability, ImageAttachment, Message
from yagami.backends.llama_cpp import LlamaCppBackend
from yagami.backends.ollama import OllamaBackend
from yagami.backends.openai import OpenAIBackend
from yagami.backends.openai_compat import OpenAICompatBackend
from yagami.backends.stability import StabilityImageBackend
from yagami.config import (
    AnthropicConfig,
    LlamaCppConfig,
    OllamaConfig,
    OpenAIConfig,
    StabilityConfig,
)


async def _collect(backend, messages: list[Message], options: BackendOptions | None = None):
    return [
        chunk async for chunk in backend.generate(messages, options=options or BackendOptions())
    ]


class _AsyncEvents:
    def __init__(self, events):
        self._events = events

    def __aiter__(self):
        self._iterator = iter(self._events)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _OpenAICompletions:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        no_choices = SimpleNamespace(choices=[])
        text_delta = SimpleNamespace(content="hello", tool_calls=[])
        text = SimpleNamespace(choices=[SimpleNamespace(delta=text_delta)])
        function = SimpleNamespace(name="calc", arguments='{"expression":"2+2"}')
        call = SimpleNamespace(index=0, id="call-1", function=function)
        tool = SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[call]))]
        )
        return _AsyncEvents([no_choices, text, tool])


class _ClosableClient:
    def __init__(self, completions: _OpenAICompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["openai", "compatible"])
async def test_openai_wire_adapters_translate_multimodal_tools(kind):
    if kind == "openai":
        backend = OpenAIBackend(OpenAIConfig(), "test-key")
    else:
        backend = OpenAICompatBackend(
            api_key="test-key",
            base_url="https://provider.invalid/v1",
            model="test-model",
            max_tokens=512,
            capabilities={Capability.TEXT, Capability.TOOLS},
        )
        backend.name = "compatible"
    completions = _OpenAICompletions()
    client = _ClosableClient(completions)
    backend._client = client
    image = ImageAttachment(media_type="image/png", data_b64="aGVsbG8=")
    messages = [
        Message(role="system", content="original system"),
        Message(role="user", content="look", images=[image]),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "previous-call",
                    "type": "function",
                    "function": {"name": "calc", "arguments": "{}"},
                }
            ],
        ),
        Message(role="tool", content="4", tool_call_id="previous-call"),
    ]
    options = BackendOptions(
        system_prompt="replacement system",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "calc",
                    "description": "calculate",
                    "parameters": {"type": "object"},
                },
            }
        ],
        tool_choice="required",
    )

    chunks = await _collect(backend, messages, options)

    assert [chunk["type"] for chunk in chunks] == ["text", "tool_call", "done"]
    assert completions.kwargs["messages"][0]["content"] == "replacement system"
    assert completions.kwargs["messages"][1]["content"][0]["type"] == "image_url"
    assert completions.kwargs["messages"][2]["tool_calls"][0]["id"] == "previous-call"
    assert completions.kwargs["messages"][3]["tool_call_id"] == "previous-call"
    assert completions.kwargs["tools"] == options.tools
    assert chunks[1]["meta"]["name"] == "calc"
    assert await backend.health()
    await backend.close()
    assert client.closed


class _AnthropicStream:
    text_stream = _AsyncEvents(["one", " two"])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _AnthropicMessages:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="tool please"),
                SimpleNamespace(type="tool_use", id="tool-1", name="calc", input={"x": 2}),
            ]
        )

    def stream(self, **kwargs):
        self.kwargs = kwargs
        return _AnthropicStream()


class _AnthropicClient:
    def __init__(self) -> None:
        self.messages = _AnthropicMessages()
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_anthropic_adapter_translates_images_history_and_tools():
    backend = ClaudeBackend(AnthropicConfig(), "test-key")
    client = _AnthropicClient()
    backend._client = client
    messages = [
        Message(role="system", content="safe system"),
        Message(
            role="user",
            content="inspect",
            images=[ImageAttachment(media_type="image/png", data_b64="aGVsbG8=")],
        ),
        Message(
            role="assistant",
            content="calling",
            tool_calls=[
                {
                    "id": "old",
                    "type": "function",
                    "function": {"name": "calc", "arguments": "not-json"},
                }
            ],
        ),
        Message(role="tool", content="4", tool_call_id="old"),
    ]
    tool = {
        "type": "function",
        "function": {
            "name": "calc",
            "description": "calculate",
            "parameters": {"type": "object", "properties": {}},
        },
    }

    chunks = await _collect(
        backend,
        messages,
        BackendOptions(tools=[tool], tool_choice={"function": {"name": "calc"}}),
    )

    assert [chunk["type"] for chunk in chunks] == ["text", "tool_call", "done"]
    assert client.messages.kwargs["system"] == "safe system"
    assert client.messages.kwargs["messages"][0]["content"][0]["type"] == "image"
    assert client.messages.kwargs["messages"][1]["content"][1]["input"] == {}
    assert client.messages.kwargs["messages"][2]["content"][0]["type"] == "tool_result"
    assert client.messages.kwargs["tool_choice"] == {"type": "tool", "name": "calc"}
    assert chunks[1]["meta"]["arguments"] == '{"x":2}'
    assert await backend.health()
    await backend.close()
    assert client.closed


@pytest.mark.asyncio
async def test_anthropic_adapter_streams_without_tools():
    backend = ClaudeBackend(AnthropicConfig(), "test-key")
    client = _AnthropicClient()
    backend._client = client

    chunks = await _collect(backend, [Message(role="user", content="hello")])

    assert "".join(chunk["content"] for chunk in chunks if chunk["type"] == "text") == "one two"
    assert chunks[-1]["type"] == "done"


class _Llama:
    def create_chat_completion(self, **kwargs):
        self.kwargs = kwargs
        return [
            {"choices": [{"delta": {"content": "local"}}]},
            {"choices": [{"delta": {}}]},
        ]


@pytest.mark.asyncio
async def test_llama_cpp_adapter_streams_and_reports_load_failure(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fake")
    backend = LlamaCppBackend(LlamaCppConfig(model_path=str(model)))
    llm = _Llama()
    backend._llm = llm

    chunks = await _collect(
        backend,
        [Message(role="system", content="old"), Message(role="user", content="hello")],
        BackendOptions(system_prompt="new"),
    )

    assert [chunk["type"] for chunk in chunks] == ["text", "done"]
    assert llm.kwargs["messages"][0] == {"role": "system", "content": "new"}
    assert await backend.health()

    broken = LlamaCppBackend(LlamaCppConfig(model_path=str(model)))
    broken._load = lambda: (_ for _ in ()).throw(RuntimeError("missing runtime"))
    errors = await _collect(broken, [Message(role="user", content="hello")])
    assert [chunk["type"] for chunk in errors] == ["error", "done"]


@pytest.mark.asyncio
async def test_ollama_adapter_stream_health_and_error_paths():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(
            200,
            text='{"message":{"content":"local"}}\n\n{"done":true}\n',
        )

    backend = OllamaBackend(OllamaConfig())
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(handler)
    )
    chunks = await _collect(
        backend,
        [Message(role="system", content="old"), Message(role="user", content="hello")],
        BackendOptions(system_prompt="new", model_override="test-model"),
    )
    assert [chunk["type"] for chunk in chunks] == ["text", "done"]
    assert chunks[0]["meta"]["model"] == "test-model"
    assert await backend.health()
    await backend.close()

    async def failing(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    offline = OllamaBackend(OllamaConfig())
    await offline._client.aclose()
    offline._client = httpx.AsyncClient(
        base_url="http://ollama.test", transport=httpx.MockTransport(failing)
    )
    assert not await offline.health()
    errors = await _collect(offline, [Message(role="user", content="hello")])
    assert [chunk["type"] for chunk in errors] == ["error", "done"]
    await offline.close()


@pytest.mark.asyncio
async def test_stability_adapter_handles_success_empty_prompt_and_http_error():
    async def success(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"png-bytes")

    backend = StabilityImageBackend(StabilityConfig(), "test-key")
    await backend._client.aclose()
    backend._client = httpx.AsyncClient(
        base_url="https://api.stability.ai", transport=httpx.MockTransport(success)
    )
    chunks = await _collect(backend, [Message(role="user", content="a private landscape")])
    assert [chunk["type"] for chunk in chunks] == ["image_url", "done"]
    assert chunks[0]["content"].startswith("data:image/png;base64,")
    assert await backend.health()
    await backend.close()

    empty = StabilityImageBackend(StabilityConfig(), "test-key")
    empty_chunks = await _collect(empty, [Message(role="assistant", content="no prompt")])
    assert empty_chunks == [{"type": "error", "content": "empty prompt", "meta": {}}]
    await empty.close()

    async def failure(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    failed = StabilityImageBackend(StabilityConfig(), "test-key")
    await failed._client.aclose()
    failed._client = httpx.AsyncClient(
        base_url="https://api.stability.ai", transport=httpx.MockTransport(failure)
    )
    errors = await _collect(failed, [Message(role="user", content="draw")])
    assert [chunk["type"] for chunk in errors] == ["error", "done"]
    await failed.close()
