"""DB helpers for the observations table.

`queue_observation()` is the write-path entry point called from stream.py
after each turn. It applies the write gate, chunks long text, inserts each
chunk with embedding_status='pending', and returns the inserted row ids.
The background worker (worker.py) picks them up.
"""

from __future__ import annotations

import logging
import struct
import time
from typing import Iterable

from ..router.schema import Sensitivity
from ..storage.db import get_db
from .chunker import chunk

log = logging.getLogger("yagami.memory.store")

# TTL knobs (millis from now). Keep short for PHI, generous default for
# everything else. Vacuum job in v0.2.16 reads ttl_until.
_DAY_MS = 24 * 60 * 60 * 1000
TTL_PHI_MS = 7 * _DAY_MS
TTL_DEFAULT_MS = 90 * _DAY_MS

# Don't store anything shorter than this - captures "thanks", "lol", "ok"
# without paying a row + embedding for them.
MIN_REMEMBER_CHARS = 20


def _ttl_for(sens: Sensitivity) -> int | None:
    if sens in (Sensitivity.PHI, Sensitivity.PHI_MEDICAL):
        return int(time.time() * 1000) + TTL_PHI_MS
    return int(time.time() * 1000) + TTL_DEFAULT_MS


def _vec_blob(vec: list[float]) -> bytes:
    """sqlite-vec accepts float32 little-endian byte strings for vec0 inserts."""
    return struct.pack(f"<{len(vec)}f", *vec)


async def queue_observation(
    *,
    session_id: str,
    role: str,
    text: str,
    sensitivity: Sensitivity,
    source_app: str = "chat",
) -> list[int]:
    """Apply the write gate and insert pending observations.

    Returns the list of inserted row ids (empty if the gate rejected).
    """
    if sensitivity == Sensitivity.SECRET:
        return []  # never written. Defense in depth.
    text = text.strip()
    if len(text) < MIN_REMEMBER_CHARS:
        return []

    db = get_db()
    chunks = chunk(text)
    if not chunks:
        return []

    now = int(time.time() * 1000)
    ttl = _ttl_for(sensitivity)
    ids: list[int] = []
    parent_id: int | None = None
    for i, ch in enumerate(chunks):
        cur = await db.execute(
            """INSERT INTO observations
                 (session_id, role, text, sensitivity, source_app,
                  ttl_until, created_at, chunk_index, parent_id, embedding_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                session_id,
                role,
                ch,
                sensitivity.value,
                source_app,
                ttl,
                now,
                i,
                parent_id,
            ),
        )
        new_id = cur.lastrowid
        assert new_id is not None
        if i == 0:
            parent_id = new_id
        ids.append(new_id)
    await db.commit()
    return ids


async def list_pending(limit: int = 32) -> list[tuple[int, str]]:
    """Worker entry: rows that need embeddings. Returns (id, text) tuples."""
    db = get_db()
    async with db.execute(
        "SELECT id, text FROM observations"
        " WHERE embedding_status = 'pending'"
        " ORDER BY id ASC LIMIT ?",
        (limit,),
    ) as cur:
        rows = await cur.fetchall()
    return [(int(r[0]), str(r[1])) for r in rows]


async def write_embeddings(rows: Iterable[tuple[int, list[float] | None]]) -> None:
    """Persist embedder output. None → mark failed, vector → insert into vec0."""
    db = get_db()
    for obs_id, vec in rows:
        if vec is None:
            await db.execute(
                "UPDATE observations SET embedding_status='failed' WHERE id=?",
                (obs_id,),
            )
            continue
        try:
            await db.execute(
                "INSERT INTO observations_vec(rowid, embedding) VALUES (?, ?)",
                (obs_id, _vec_blob(vec)),
            )
            await db.execute(
                "UPDATE observations SET embedding_status='ready' WHERE id=?",
                (obs_id,),
            )
        except Exception as exc:  # noqa: BLE001 - vec insert failure shouldn't crash
            log.warning("vec insert failed for obs %s: %s", obs_id, exc)
            await db.execute(
                "UPDATE observations SET embedding_status='failed' WHERE id=?",
                (obs_id,),
            )
    await db.commit()


async def count_by_status() -> dict[str, int]:
    """Diagnostic - how many rows in each embedding state."""
    db = get_db()
    out: dict[str, int] = {}
    async with db.execute(
        "SELECT embedding_status, COUNT(*) FROM observations GROUP BY embedding_status"
    ) as cur:
        async for row in cur:
            out[str(row[0])] = int(row[1])
    return out


async def delete_expired(now_ms: int | None = None) -> int:
    """Remove observations whose ttl_until has passed. Returns deleted count.
    Called by the vacuum job (v0.2.16); exposed here so the write tests can
    exercise it without spinning up a scheduler."""
    db = get_db()
    cutoff = now_ms if now_ms is not None else int(time.time() * 1000)
    cur = await db.execute(
        "SELECT id FROM observations WHERE ttl_until IS NOT NULL AND ttl_until < ?",
        (cutoff,),
    )
    ids = [int(r[0]) async for r in cur]
    if not ids:
        return 0
    qmarks = ",".join("?" * len(ids))
    await db.execute(f"DELETE FROM observations_vec WHERE rowid IN ({qmarks})", ids)
    await db.execute(f"DELETE FROM observations WHERE id IN ({qmarks})", ids)
    await db.commit()
    return len(ids)
