from __future__ import annotations

import pytest

from yagami.backends.base import Message
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
