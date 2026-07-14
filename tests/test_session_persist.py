from __future__ import annotations

import pytest
from pydantic import ValidationError

from yagami.api.sessions import RenameBody
from yagami.backends.base import ImageAttachment, Message
from yagami.chat.session import SessionStore


@pytest.mark.asyncio
async def test_new_session_persists(fresh_db):
    store = SessionStore()
    sid = await store.new_session()
    assert await store.session_exists(sid)


@pytest.mark.asyncio
async def test_append_and_history_round_trip(fresh_db):
    store = SessionStore()
    sid = await store.new_session()
    await store.append(sid, Message(role="user", content="hi"))
    await store.append(sid, Message(role="assistant", content="hello"))
    history = await store.history(sid)
    assert [m.role for m in history] == ["user", "assistant"]
    assert [m.content for m in history] == ["hi", "hello"]


@pytest.mark.asyncio
async def test_list_sessions_order_by_updated(fresh_db):
    store = SessionStore()
    s1 = await store.new_session()
    s2 = await store.new_session()
    await store.append(s1, Message(role="user", content="ping"))
    rows = await store.list_sessions()
    assert rows[0]["id"] == s1
    assert rows[1]["id"] == s2


@pytest.mark.asyncio
async def test_first_user_message_becomes_title(fresh_db):
    store = SessionStore()
    sid = await store.new_session()
    await store.append(sid, Message(role="user", content="design a distributed cache"))
    rows = await store.list_sessions()
    assert rows[0]["title"].startswith("design a distributed cache")


@pytest.mark.asyncio
async def test_image_only_message_gets_useful_title(fresh_db):
    store = SessionStore()
    sid = await store.new_session()
    image = ImageAttachment(media_type="image/png", data_b64="aGVsbG8=")
    await store.append(sid, Message(role="user", content="", images=[image]))
    rows = await store.list_sessions()
    assert rows[0]["title"] == "Image attachment"


@pytest.mark.asyncio
async def test_image_attachments_round_trip_and_cascade(fresh_db):
    store = SessionStore()
    sid = await store.new_session()
    images = [
        ImageAttachment(media_type="image/png", data_b64="aGVsbG8="),
        ImageAttachment(media_type="image/jpeg", data_b64="d29ybGQ="),
    ]
    await store.append(sid, Message(role="user", content="compare these", images=images))

    history = await store.history(sid)
    assert history[0].images == images

    assert await store.delete(sid)
    async with fresh_db.execute("SELECT COUNT(*) FROM message_attachments") as cur:
        assert (await cur.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_delete_message_images_preserves_message(fresh_db):
    store = SessionStore()
    sid = await store.new_session()
    image = ImageAttachment(media_type="image/png", data_b64="aGVsbG8=")
    message_id = await store.append(sid, Message(role="user", content="image", images=[image]))

    await store.delete_message_images(message_id)

    history = await store.history(sid)
    assert history == [Message(role="user", content="image")]


def test_rename_body_strips_whitespace_and_enforces_storage_limit():
    assert RenameBody(title="  useful title  ").title == "useful title"
    with pytest.raises(ValidationError):
        RenameBody(title="   ")
    with pytest.raises(ValidationError):
        RenameBody(title="x" * 121)
