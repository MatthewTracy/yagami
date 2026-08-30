from __future__ import annotations

import pytest

from yagami.backends.base import Backend, BackendOptions, Capability, Message
from yagami.backends.echo import EchoBackend
from yagami.backends.ollama import OllamaBackend


def test_echo_is_backend():
    assert isinstance(EchoBackend(), Backend)


@pytest.mark.asyncio
async def test_echo_streams_text():
    eb = EchoBackend()
    chunks = []
    async for c in eb.generate(
        [Message(role="user", content="hi there")], options=BackendOptions()
    ):
        chunks.append(c)
    text = "".join(c["content"] for c in chunks if c["type"] == "text")
    assert "Policy-only demo" in text
    assert "hello" not in text
    assert chunks[-1]["type"] == "done"


def test_capability_enum_strings():
    assert Capability.TEXT.value == "text"
    assert Capability.IMAGE.value == "image"


def test_ollama_advertises_native_tool_calling():
    assert Capability.TOOLS in OllamaBackend.capabilities
