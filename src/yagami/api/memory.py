"""Browser-facing endpoints for the cross-session memory store.

GET  /api/memory                — list recent observations, paginated
GET  /api/memory/search?q=...   — keyword search via FTS5 (no embed call needed)
DELETE /api/memory/{id}         — remove a single observation + its vec row
GET  /api/memory/stats          — counts by status, total bytes

PHI rows are NOT redacted in this endpoint — the user is reviewing their
OWN memory in their OWN browser, on-device. Surfacing PHI to the user
is correct; surfacing it to a cloud-text turn is not (the retriever's
PHI quarantine handles that).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..memory import store as memory_store
from ..storage.db import get_db

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("")
async def list_observations(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    db = get_db()
    async with db.execute(
        """SELECT id, session_id, role, text, sensitivity, source_app,
                  ttl_until, created_at, embedding_status, chunk_index, parent_id
           FROM observations
           ORDER BY id DESC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    ) as cur:
        rows = await cur.fetchall()
    return {
        "observations": [
            {
                "id": int(r[0]),
                "session_id": str(r[1]),
                "role": str(r[2]),
                "text": str(r[3]),
                "sensitivity": str(r[4]),
                "source_app": str(r[5]),
                "ttl_until": r[6],
                "created_at": int(r[7]),
                "embedding_status": str(r[8]),
                "chunk_index": int(r[9]),
                "parent_id": r[10],
            }
            for r in rows
        ],
        "count": len(rows),
    }


@router.get("/search")
async def search(
    q: str = Query(min_length=1, max_length=500),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    db = get_db()
    cleaned = q.replace('"', "").strip()
    if not cleaned:
        return {"observations": [], "count": 0}
    try:
        async with db.execute(
            """SELECT o.id, o.session_id, o.role, o.text, o.sensitivity,
                      o.created_at, o.embedding_status
               FROM observations_fts f
               JOIN observations o ON o.id = f.rowid
               WHERE f.text MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (cleaned, limit),
        ) as cur:
            rows = await cur.fetchall()
    except Exception as exc:  # noqa: BLE001 — FTS MATCH parser is picky
        raise HTTPException(400, f"search failed: {exc}")
    return {
        "observations": [
            {
                "id": int(r[0]),
                "session_id": str(r[1]),
                "role": str(r[2]),
                "text": str(r[3]),
                "sensitivity": str(r[4]),
                "created_at": int(r[5]),
                "embedding_status": str(r[6]),
            }
            for r in rows
        ],
        "count": len(rows),
    }


@router.delete("/{observation_id}")
async def delete_observation(observation_id: int) -> dict:
    db = get_db()
    async with db.execute("SELECT 1 FROM observations WHERE id=?", (observation_id,)) as cur:
        if await cur.fetchone() is None:
            raise HTTPException(404, f"observation {observation_id} not found")
    await db.execute("DELETE FROM observations_vec WHERE rowid=?", (observation_id,))
    await db.execute("DELETE FROM observations WHERE id=?", (observation_id,))
    await db.commit()
    return {"ok": True, "deleted": observation_id}


@router.get("/stats")
async def memory_stats() -> dict:
    counts = await memory_store.count_by_status()
    db = get_db()
    async with db.execute("SELECT COUNT(*) FROM observations") as cur:
        total = (await cur.fetchone())[0]
    async with db.execute("SELECT COUNT(*) FROM observations_vec") as cur:
        vec_total = (await cur.fetchone())[0]
    return {
        "total": int(total),
        "vec_total": int(vec_total),
        "by_status": counts,
    }
