from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Callable

import pytest
from fastapi import WebSocketDisconnect

from yagami.backends.base import (
    BackendChunk,
    BackendOptions,
    Capability,
    Message,
    Pricing,
)
from yagami.chat.session import SessionStore
from yagami.chat.stream import chat_endpoint
from yagami.config import RoutingConfig
from yagami.router.policy import RoutingPolicy

_DISCONNECT = object()


class _FakeWebSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive_json(self):
        item = await self.incoming.get()
        if item is _DISCONNECT:
            raise WebSocketDisconnect()
        return item

    async def send_text(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def wait_for(self, predicate: Callable[[dict], bool], timeout: float = 2.0) -> dict:
        async def poll() -> dict:
            while True:
                for message in self.sent:
                    if predicate(message):
                        return message
                await asyncio.sleep(0)

        return await asyncio.wait_for(poll(), timeout=timeout)


class _SlowBackend:
    name = "ollama"
    is_local = True
    capabilities = {Capability.TEXT}
    pricing = Pricing()

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(
        self, messages: list[Message], *, options: BackendOptions
    ) -> AsyncIterator[BackendChunk]:
        self.started.set()
        try:
            await self.release.wait()
            yield {"type": "text", "content": "late", "meta": {}}
            yield {"type": "done", "content": "", "meta": {}}
        finally:
            self.cancelled.set()

    async def health(self) -> bool:
        return True


class _RecordingBackend:
    name = "ollama"
    is_local = True
    capabilities = {Capability.TEXT}
    pricing = Pricing()

    def __init__(self) -> None:
        self.calls = 0

    async def generate(
        self, messages: list[Message], *, options: BackendOptions
    ) -> AsyncIterator[BackendChunk]:
        self.calls += 1
        yield {"type": "done", "content": "", "meta": {}}

    async def health(self) -> bool:
        return True


def _policy(backend) -> RoutingPolicy:
    return RoutingPolicy(
        config=RoutingConfig(),
        backends={backend.name: backend},
        classifier=None,
    )


@pytest.mark.asyncio
async def test_cancel_interrupts_live_generation(fresh_db):
    ws = _FakeWebSocket()
    backend = _SlowBackend()
    endpoint = asyncio.create_task(chat_endpoint(ws, SessionStore(), _policy(backend)))
    await ws.wait_for(lambda m: m.get("type") == "session")

    await ws.incoming.put({"content": "keep thinking for a while"})
    await asyncio.wait_for(backend.started.wait(), timeout=2)
    await ws.incoming.put({"type": "cancel"})

    done = await ws.wait_for(
        lambda m: m.get("type") == "done" and m.get("meta", {}).get("cancelled") is True
    )
    assert done["meta"]["cancelled"] is True
    await asyncio.wait_for(backend.cancelled.wait(), timeout=2)

    await ws.incoming.put(_DISCONNECT)
    await asyncio.wait_for(endpoint, timeout=2)


@pytest.mark.asyncio
async def test_text_only_forced_backend_refuses_image_instead_of_dropping_it(fresh_db):
    ws = _FakeWebSocket()
    backend = _RecordingBackend()
    endpoint = asyncio.create_task(chat_endpoint(ws, SessionStore(), _policy(backend)))
    await ws.wait_for(lambda m: m.get("type") == "session")

    await ws.incoming.put(
        {
            "content": "describe this",
            "force_backend": "ollama",
            "images": [{"media_type": "image/png", "data_b64": "aGVsbG8="}],
        }
    )

    error = await ws.wait_for(lambda m: m.get("type") == "error")
    assert "cannot accept image" in error["content"]
    assert backend.calls == 0

    await ws.incoming.put(_DISCONNECT)
    await asyncio.wait_for(endpoint, timeout=2)
