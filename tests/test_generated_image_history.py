from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace

import pytest

from yagami.backends.base import ImageAttachment, Message
from yagami.chat.session import SessionStore
from yagami.chat.stream import _messages_for_backend, _stream_gateway, chat_endpoint

from tests.test_chat_stream import _DisconnectOnDoneWebSocket, _Gateway, _policy

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j8J8AAAAASUVORK5CYII="
URL = f"data:image/png;base64,{PNG}"


class ImageGateway:
    def __init__(self, chunks):
        self.chunks = chunks

    async def stream(self, _prepared):
        for chunk in self.chunks:
            yield chunk


@pytest.mark.asyncio
async def test_collect_generated_images_with_text_and_done():
    ws = _DisconnectOnDoneWebSocket()
    chunks = [
        {"type": "text", "content": "Two variations", "meta": {}},
        {"type": "image_url", "content": URL, "meta": {}},
        {"type": "image_url", "content": URL, "meta": {}},
        {"type": "done", "content": "", "meta": {}},
    ]
    done = asyncio.Event()
    message = await _stream_gateway(ws, ImageGateway(chunks), None, done)
    assert message.content == "Two variations"
    assert message.images == [ImageAttachment(media_type="image/png", data_b64=PNG)] * 2
    assert ws.sent == chunks
    assert done.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/private.png",
        "file:///private.png",
        "data:image/svg+xml;base64,PHN2Zz4=",
        "data:image/png;base64,not-base64!",
        "data:image/png,not-base64",
        "data:image/png;base64,",
    ],
)
async def test_invalid_generated_image_is_not_forwarded_or_saved(url):
    ws = _DisconnectOnDoneWebSocket()
    chunks = [{"type": "image_url", "content": url, "meta": {}}]
    done = asyncio.Event()
    result = await _stream_gateway(ws, ImageGateway(chunks), None, done)
    assert not result.content and result.images is None
    assert [chunk["type"] for chunk in ws.sent] == ["error", "done"]
    assert ws.sent[0]["meta"]["code"] == "invalid_generated_image"
    assert url not in str(ws.sent)
    assert done.is_set()


@pytest.mark.asyncio
async def test_too_many_generated_images_do_not_create_partial_history():
    ws = _DisconnectOnDoneWebSocket()
    chunks = [{"type": "image_url", "content": URL, "meta": {}}] * 5
    result = await _stream_gateway(ws, ImageGateway(chunks), None)
    assert result.images is None
    assert ws.sent[-2]["type"] == "error"


@pytest.mark.asyncio
async def test_oversized_generated_image_is_rejected_before_forwarding():
    ws = _DisconnectOnDoneWebSocket()
    payload = base64.b64encode(b"x" * (20 * 1024 * 1024 + 1)).decode("ascii")
    chunks = [{"type": "image_url", "content": f"data:image/png;base64,{payload}", "meta": {}}]
    result = await _stream_gateway(ws, ImageGateway(chunks), None)
    assert result.images is None
    assert [chunk["type"] for chunk in ws.sent] == ["error", "done"]


def test_generated_media_is_display_history_not_implicit_vision_input():
    image = ImageAttachment(media_type="image/png", data_b64=PNG)
    history = [
        Message(role="user", content="draw this"),
        Message(role="assistant", images=[image]),
        Message(role="assistant", content="A caption", images=[image]),
        Message(role="user", content="describe this upload", images=[image]),
    ]
    messages = _messages_for_backend(history)
    assert [m.content for m in messages] == ["draw this", "A caption", "describe this upload"]
    assert messages[1].images is None
    assert messages[2].images == [image]
    assert history[2].images == [image]
    assert _messages_for_backend(history, reset_context=True) == [history[-1]]


@pytest.mark.asyncio
async def test_generated_image_survives_disconnect_and_session_reopen(fresh_db):
    from yagami.backends.base import Capability, Pricing

    class Backend:
        name = "stability"
        is_local = False
        capabilities = {Capability.IMAGE}
        pricing = Pricing()

        async def generate(self, _messages, *, options):
            yield {"type": "image_url", "content": URL, "meta": {}}
            yield {"type": "done", "content": "", "meta": {}}

    class Gateway(_Gateway):
        async def prepare(self, **kwargs):
            prepared = await super().prepare(**kwargs)
            prepared.context = SimpleNamespace(project_id="local")
            prepared.policy.retention_days = None
            prepared.policy.policy_hash = None
            return prepared

    ws = _DisconnectOnDoneWebSocket()
    store = SessionStore()
    endpoint = asyncio.create_task(chat_endpoint(ws, store, Gateway(_policy(Backend()))))
    session = await ws.wait_for(lambda chunk: chunk.get("type") == "session")
    await ws.incoming.put({"content": "/image draw a cat"})
    await asyncio.wait_for(endpoint, timeout=3)
    restored = await SessionStore().history(session["session_id"])
    assert [m.role for m in restored] == ["user", "assistant"]
    assert restored[-1].content == ""
    assert restored[-1].images == [ImageAttachment(media_type="image/png", data_b64=PNG)]
    assert await store.delete(session["session_id"])
    async with fresh_db.execute("SELECT COUNT(*) FROM message_attachments") as cursor:
        assert (await cursor.fetchone())[0] == 0
