from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..storage.db import get_db
from ..telemetry.decisions import list_decisions

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


@router.get("")
async def get_decisions(
    session_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict:
    rows = await list_decisions(session_id=session_id, limit=limit)
    return {"decisions": rows, "count": len(rows)}


class FeedbackPayload(BaseModel):
    rating: int  # -1 (thumb down) or 1 (thumb up)


@router.post("/{decision_id}/feedback")
async def post_feedback(decision_id: int, payload: FeedbackPayload) -> dict:
    if payload.rating not in (-1, 1):
        raise HTTPException(400, "rating must be -1 or 1")
    db = get_db()
    async with db.execute("SELECT 1 FROM decisions WHERE id = ?", (decision_id,)) as cur:
        if await cur.fetchone() is None:
            raise HTTPException(404, f"decision {decision_id} not found")
    # Latest feedback for a decision wins — overwrite any prior row so the
    # user can flip their vote without leaving stale rating data.
    await db.execute("DELETE FROM feedback WHERE decision_id = ?", (decision_id,))
    await db.execute(
        "INSERT INTO feedback (decision_id, rating, created_at) VALUES (?, ?, ?)",
        (decision_id, payload.rating, int(time.time() * 1000)),
    )
    await db.commit()
    return {"ok": True, "decision_id": decision_id, "rating": payload.rating}
