from __future__ import annotations

import time
from pathlib import Path

import pytest

from yagami.memory import store
from yagami.memory.chunker import MAX_CHUNKS, TARGET_TOKENS, chunk
from yagami.memory.worker import EmbeddingWorker
from yagami.router.schema import Sensitivity
from yagami.storage.db import close_db, get_db, open_db


@pytest.fixture
async def memdb(tmp_path: Path):
    db_file = tmp_path / "mem.db"
    await open_db(db_file)
    db = get_db()
    # Insert a session row so the FK in observations doesn't bite. (The
    # observations table doesn't actually FK to sessions, but the FTS5
    # triggers fire on inserts and we want a clean slate either way.)
    await db.execute("INSERT INTO sessions(id, created_at, updated_at) VALUES ('s1', 0, 0)")
    await db.commit()
    yield db
    await close_db()


# ---- chunker ----


def test_chunk_short_returns_one():
    out = chunk("hello world")
    assert out == ["hello world"]


def test_chunk_empty_returns_empty():
    assert chunk("") == []
    assert chunk("   ") == []


def test_chunk_long_splits_below_cap():
    # 20000 chars >> target (3200) → splits, but ≤ MAX_CHUNKS chunks.
    long = ("This is a sentence. " * 1000).strip()
    out = chunk(long)
    assert 1 < len(out) <= MAX_CHUNKS
    # Every chunk respects the target plus the overlap fudge.
    for c in out:
        assert len(c) <= TARGET_TOKENS * 4 + 500


def test_chunk_respects_max_cap():
    huge = "x" * 200_000
    out = chunk(huge)
    assert len(out) <= MAX_CHUNKS


# ---- write gate / store ----


@pytest.mark.asyncio
async def test_queue_skips_secret_sessions(memdb):
    ids = await store.queue_observation(
        session_id="s1",
        role="user",
        text="here is sk-abcdefghijklmnopqrstuvwx — please rotate",
        sensitivity=Sensitivity.SECRET,
    )
    assert ids == []


@pytest.mark.asyncio
async def test_queue_skips_short_text(memdb):
    ids = await store.queue_observation(
        session_id="s1", role="user", text="lol", sensitivity=Sensitivity.NONE
    )
    assert ids == []


@pytest.mark.asyncio
async def test_queue_writes_pending_row(memdb):
    ids = await store.queue_observation(
        session_id="s1",
        role="assistant",
        text="This is a longer message that should make it past the write gate.",
        sensitivity=Sensitivity.NONE,
    )
    assert len(ids) == 1
    db = get_db()
    async with db.execute(
        "SELECT role, embedding_status, sensitivity FROM observations WHERE id=?",
        (ids[0],),
    ) as cur:
        row = await cur.fetchone()
    assert row[0] == "assistant"
    assert row[1] == "pending"
    assert row[2] == "none"


@pytest.mark.asyncio
async def test_phi_writes_with_short_ttl(memdb):
    ids = await store.queue_observation(
        session_id="s1",
        role="user",
        text="Patient Jane Doe has CHF and T2DM, please summarize.",
        sensitivity=Sensitivity.PHI_MEDICAL,
    )
    assert len(ids) == 1
    db = get_db()
    now_ms = int(time.time() * 1000)
    async with db.execute("SELECT ttl_until FROM observations WHERE id=?", (ids[0],)) as cur:
        row = await cur.fetchone()
    ttl = row[0]
    # PHI TTL ≈ 7 days, default ≈ 90 days. Use the midpoint to distinguish.
    assert ttl is not None
    days = (ttl - now_ms) / (24 * 60 * 60 * 1000)
    assert 6 < days < 8


@pytest.mark.asyncio
async def test_chunked_inserts_share_parent_id(memdb):
    long = ("Important context line. " * 500).strip()
    ids = await store.queue_observation(
        session_id="s1", role="assistant", text=long, sensitivity=Sensitivity.NONE
    )
    assert len(ids) > 1
    db = get_db()
    # First chunk has parent_id NULL, the rest reference it.
    async with db.execute(
        "SELECT id, chunk_index, parent_id FROM observations WHERE id IN ({})".format(
            ",".join(str(i) for i in ids)
        )
    ) as cur:
        rows = [tuple(r) async for r in cur]
    rows.sort(key=lambda r: r[1])
    assert rows[0][2] is None  # first chunk = no parent
    parent_id = rows[0][0]
    for _id, idx, parent in rows[1:]:
        assert parent == parent_id, f"chunk {idx} should point at {parent_id}, got {parent}"


@pytest.mark.asyncio
async def test_delete_expired_drops_old_rows(memdb):
    # Insert two rows: one with ttl in the past, one with ttl in the future.
    db = get_db()
    now = int(time.time() * 1000)
    await db.execute(
        """INSERT INTO observations
             (session_id, role, text, sensitivity, source_app,
              ttl_until, created_at, chunk_index, parent_id, embedding_status)
           VALUES ('s1','user','old enough to expire','none','chat',?,?,0,NULL,'ready')""",
        (now - 1000, now - 100000),
    )
    await db.execute(
        """INSERT INTO observations
             (session_id, role, text, sensitivity, source_app,
              ttl_until, created_at, chunk_index, parent_id, embedding_status)
           VALUES ('s1','user','still fresh enough to keep','none','chat',?,?,0,NULL,'ready')""",
        (now + 1_000_000, now),
    )
    await db.commit()

    deleted = await store.delete_expired()
    assert deleted == 1

    async with db.execute("SELECT COUNT(*) FROM observations") as cur:
        row = await cur.fetchone()
    assert row[0] == 1


# ---- worker ----


class FakeEmbedder:
    def __init__(self, fail_for: set[int] | None = None) -> None:
        self._fail_for = fail_for or set()
        self.calls = 0
        self.model = "fake"

    async def embed(self, text: str):
        self.calls += 1
        if self.calls in self._fail_for:
            return None
        # Deterministic 384-dim "embedding" so the vec table accepts it.
        return [float(hash(text) % 1000) / 1000.0] * 384


@pytest.mark.asyncio
async def test_worker_embeds_pending_rows(memdb):
    ids = await store.queue_observation(
        session_id="s1",
        role="assistant",
        text="Worker should pick this up and mark ready.",
        sensitivity=Sensitivity.NONE,
    )
    assert len(ids) == 1

    w = EmbeddingWorker(FakeEmbedder())
    n = await w._drain_once()
    assert n == 1

    db = get_db()
    async with db.execute("SELECT embedding_status FROM observations WHERE id=?", (ids[0],)) as cur:
        row = await cur.fetchone()
    assert row[0] == "ready"
    # And the vec row exists.
    async with db.execute("SELECT COUNT(*) FROM observations_vec WHERE rowid=?", (ids[0],)) as cur:
        row = await cur.fetchone()
    assert row[0] == 1


@pytest.mark.asyncio
async def test_worker_marks_failed_when_embedder_returns_none(memdb):
    ids = await store.queue_observation(
        session_id="s1",
        role="assistant",
        text="This one's embedder will return None.",
        sensitivity=Sensitivity.NONE,
    )
    w = EmbeddingWorker(FakeEmbedder(fail_for={1}))
    await w._drain_once()
    db = get_db()
    async with db.execute("SELECT embedding_status FROM observations WHERE id=?", (ids[0],)) as cur:
        row = await cur.fetchone()
    assert row[0] == "failed"


@pytest.mark.asyncio
async def test_worker_drain_is_idempotent_on_empty(memdb):
    w = EmbeddingWorker(FakeEmbedder())
    n = await w._drain_once()
    assert n == 0


@pytest.mark.asyncio
async def test_worker_nudge_does_not_raise(memdb):
    w = EmbeddingWorker(FakeEmbedder())
    # No background task running — nudge should set the event without crashing.
    w.nudge()
    assert w._wake.is_set()


@pytest.mark.asyncio
async def test_count_by_status_aggregates(memdb):
    await store.queue_observation(
        session_id="s1",
        role="user",
        text="A row that should land as pending.",
        sensitivity=Sensitivity.NONE,
    )
    counts = await store.count_by_status()
    assert counts.get("pending", 0) >= 1
