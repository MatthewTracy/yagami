from __future__ import annotations

import json

import pytest

from yagami.backends.base import ImageAttachment, Message
from yagami.chat.session import SessionStore
from yagami.privacy import (
    cleanup_expired_sessions,
    cleanup_policy_retention,
    data_counts,
    purge_data,
    stream_export,
)
from yagami.storage.db import now_ms


async def _collect_export() -> dict:
    return json.loads("".join([chunk async for chunk in stream_export()]))


@pytest.mark.asyncio
async def test_retention_zero_preserves_sessions(fresh_db):
    store = SessionStore()
    sid = await store.new_session()
    await fresh_db.execute("UPDATE sessions SET updated_at=0 WHERE id=?", (sid,))
    await fresh_db.commit()

    assert await cleanup_expired_sessions(0) == 0
    assert await store.session_exists(sid)


@pytest.mark.asyncio
async def test_retention_deletes_stale_session_and_derived_memory(fresh_db):
    store = SessionStore()
    stale = await store.new_session()
    current = await store.new_session()
    await fresh_db.execute("UPDATE sessions SET updated_at=0 WHERE id=?", (stale,))
    await fresh_db.execute(
        """INSERT INTO observations
           (session_id, role, text, sensitivity, source_app, ttl_until,
            created_at, chunk_index, parent_id, embedding_status)
           VALUES (?, 'user', 'remember me', 'none', 'chat', NULL, ?, 0, NULL, 'pending')""",
        (stale, now_ms()),
    )
    await fresh_db.commit()

    assert await cleanup_expired_sessions(30) == 1
    assert not await store.session_exists(stale)
    assert await store.session_exists(current)
    async with fresh_db.execute("SELECT COUNT(*) FROM observations") as cur:
        assert (await cur.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_policy_receipt_retention_deletes_only_expired_decisions(fresh_db):
    store = SessionStore()
    sid = await store.new_session()
    receipt = json.dumps({"retention_days": 7})
    old = now_ms() - 8 * 24 * 60 * 60 * 1000
    recent = now_ms() - 1 * 24 * 60 * 60 * 1000
    for created_at in (old, recent):
        await fresh_db.execute(
            "INSERT INTO decisions("
            "session_id, created_at, backend, is_local, reason, classification,"
            "scrubbed_preview, source, policy_decision"
            ") VALUES(?, ?, 'ollama', 1, 'test', '{}', '', 'test', ?)",
            (sid, created_at, receipt),
        )
    await fresh_db.commit()

    assert await cleanup_policy_retention() == 1
    async with fresh_db.execute("SELECT created_at FROM decisions") as cursor:
        remaining = [int(row[0]) async for row in cursor]
    assert remaining == [recent]


@pytest.mark.asyncio
async def test_purge_scope_can_preserve_or_delete_knowledge_base(fresh_db):
    store = SessionStore()
    sid = await store.new_session()
    await store.append(sid, Message(role="user", content="private chat"))
    await fresh_db.execute(
        "INSERT INTO kb_documents(source_path, chunk_index, text, created_at, embedding_status)"
        " VALUES('guide.txt', 0, 'reference', ?, 'pending')",
        (now_ms(),),
    )
    await fresh_db.commit()

    deleted = await purge_data(include_knowledge_base=False)
    assert deleted["sessions"] == 1
    counts = await data_counts()
    assert counts["sessions"] == 0
    assert counts["kb_documents"] == 1

    await purge_data(include_knowledge_base=True)
    assert (await data_counts())["kb_documents"] == 0


@pytest.mark.asyncio
async def test_full_export_includes_portable_image_data(fresh_db):
    store = SessionStore()
    sid = await store.new_session()
    image = ImageAttachment(media_type="image/png", data_b64="aGVsbG8=")
    await store.append(sid, Message(role="user", content="look", images=[image]))

    exported = await _collect_export()

    assert exported["format"] == "yagami-export"
    assert exported["version"] == 1
    assert exported["tables"]["sessions"][0]["id"] == sid
    attachment = exported["tables"]["message_attachments"][0]
    assert attachment["media_type"] == "image/png"
    assert attachment["data_b64"] == "aGVsbG8="
    assert "data" not in attachment
