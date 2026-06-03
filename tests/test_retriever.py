from __future__ import annotations

import struct
import time
from pathlib import Path

import pytest

from yagami.memory.retriever import Retriever
from yagami.router.schema import Sensitivity
from yagami.storage.db import close_db, get_db, open_db


def _vec_blob(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


class _FakeEmbedder:
    """Returns a fixed embedding per text - same text → same vector → distance 0.
    Different texts → orthogonal-ish vectors so distance differs."""

    def __init__(self) -> None:
        self.model = "fake"
        self._cache: dict[str, list[float]] = {}

    async def embed(self, text: str):
        if text in self._cache:
            return self._cache[text]
        # Hash-derived deterministic vector; first dim varies with text so
        # different strings land at different distances.
        seed = abs(hash(text)) % 1000
        vec = [float(seed) / 1000.0] + [0.0] * 383
        self._cache[text] = vec
        return vec


async def _insert(
    session_id: str,
    role: str,
    text: str,
    sens: Sensitivity = Sensitivity.NONE,
    embedding: list[float] | None = None,
) -> int:
    db = get_db()
    now = int(time.time() * 1000)
    cur = await db.execute(
        """INSERT INTO observations
             (session_id, role, text, sensitivity, source_app,
              ttl_until, created_at, chunk_index, parent_id, embedding_status)
           VALUES (?, ?, ?, ?, 'chat', ?, ?, 0, NULL, ?)""",
        (
            session_id,
            role,
            text,
            sens.value,
            now + 90 * 24 * 3600 * 1000,
            now,
            "ready" if embedding is not None else "pending",
        ),
    )
    obs_id = int(cur.lastrowid)
    if embedding is not None:
        await db.execute(
            "INSERT INTO observations_vec(rowid, embedding) VALUES (?, ?)",
            (obs_id, _vec_blob(embedding)),
        )
    await db.commit()
    return obs_id


@pytest.fixture
async def memdb(tmp_path: Path):
    await open_db(tmp_path / "mem.db")
    db = get_db()
    await db.execute("INSERT INTO sessions(id, created_at, updated_at) VALUES ('s1', 0, 0)")
    await db.execute("INSERT INTO sessions(id, created_at, updated_at) VALUES ('s2', 0, 0)")
    await db.commit()
    yield db
    await close_db()


@pytest.mark.asyncio
async def test_empty_query_returns_nothing(memdb):
    r = Retriever(_FakeEmbedder())
    assert await r.fetch("") == []
    assert await r.fetch("   ") == []


@pytest.mark.asyncio
async def test_vec_search_returns_closest(memdb):
    emb = _FakeEmbedder()
    # Insert two embeddings: one matching "dog", one not.
    vec_dog = await emb.embed("my dog is mango")
    vec_other = await emb.embed("today the weather is fine")
    await _insert("s1", "user", "my dog is mango", embedding=vec_dog)
    await _insert("s1", "assistant", "ok, noted - Mango", embedding=vec_other)

    r = Retriever(emb)
    hits = await r.fetch("my dog is mango", k=5, exclude_session="s2")
    assert len(hits) > 0
    # The matching row should have distance 0 (same embedding).
    closest = min(hits, key=lambda h: h.distance if h.distance is not None else 1e9)
    assert closest.text == "my dog is mango"
    assert closest.source == "vec"


@pytest.mark.asyncio
async def test_exclude_session_drops_same_session(memdb):
    emb = _FakeEmbedder()
    vec = await emb.embed("note")
    await _insert("s1", "user", "note in session 1", embedding=vec)
    await _insert("s2", "user", "note in session 2", embedding=vec)

    r = Retriever(emb)
    hits = await r.fetch("note", k=10, exclude_session="s1")
    sids = {h.session_id for h in hits}
    assert "s1" not in sids
    assert "s2" in sids


@pytest.mark.asyncio
async def test_phi_quarantine_drops_phi_when_current_is_clean(memdb):
    emb = _FakeEmbedder()
    vec = await emb.embed("query")
    await _insert("s1", "user", "Patient Jenny CHF + T2DM", Sensitivity.PHI_MEDICAL, embedding=vec)
    await _insert("s1", "assistant", "the cat is sleeping", Sensitivity.NONE, embedding=vec)

    r = Retriever(emb)
    hits = await r.fetch("query", k=10, exclude_session="other", current_sens=Sensitivity.NONE)
    assert all(h.sensitivity == Sensitivity.NONE for h in hits)


@pytest.mark.asyncio
async def test_phi_quarantine_allows_phi_in_phi_session(memdb):
    emb = _FakeEmbedder()
    vec = await emb.embed("query")
    await _insert(
        "s1",
        "user",
        "Patient Jenny CHF + T2DM",
        Sensitivity.PHI_MEDICAL,
        embedding=vec,
    )

    r = Retriever(emb)
    hits = await r.fetch(
        "query", k=5, exclude_session="other", current_sens=Sensitivity.PHI_MEDICAL
    )
    assert any(h.sensitivity == Sensitivity.PHI_MEDICAL for h in hits)


@pytest.mark.asyncio
async def test_fts_fallback_for_pending_rows(memdb):
    emb = _FakeEmbedder()
    # Insert a row WITHOUT an embedding - it should still appear via FTS.
    await _insert("s1", "user", "I love writing haiku about mango trees", embedding=None)

    r = Retriever(emb)
    hits = await r.fetch("haiku mango", k=5, exclude_session="other")
    assert any(h.source == "fts" for h in hits)
    assert any("mango" in h.text for h in hits)


@pytest.mark.asyncio
async def test_fts_handles_quotes_safely(memdb):
    emb = _FakeEmbedder()
    await _insert("s1", "user", "test row", embedding=None)
    r = Retriever(emb)
    # Double quote in query MUST NOT crash FTS5 MATCH parser.
    hits = await r.fetch('look for "quoted" stuff', k=5, exclude_session="other")
    # The exact result doesn't matter - just that it didn't throw.
    assert isinstance(hits, list)
