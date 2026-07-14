from __future__ import annotations

import re
import time

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from ..storage.db import get_db
from ..telemetry.decisions import export_decisions_csv, list_decisions

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


@router.get("")
async def get_decisions(
    session_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict:
    rows = await list_decisions(session_id=session_id, limit=limit)
    return {"decisions": rows, "count": len(rows)}


@router.get("/export")
async def export_decisions(
    session_id: str | None = Query(default=None),
    limit: int = Query(default=10_000, ge=1, le=100_000),
) -> Response:
    """Download the Privacy Ledger as CSV - see telemetry/decisions.py for
    what's included. Same scrubbing as the UI ledger view; nothing extra
    leaks through this export."""
    csv_text = await export_decisions_csv(session_id=session_id, limit=limit)
    # session_id is caller-supplied; never interpolate it into a header
    # unsanitized. Session ids are uuid4().hex (see chat/session.py) - a
    # non-matching value just falls back to the unscoped filename.
    suffix = ""
    if session_id and re.fullmatch(r"[a-f0-9]{8,64}", session_id):
        suffix = f"-{session_id}"
    filename = f"yagami-privacy-ledger{suffix}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
    # Latest feedback for a decision wins - overwrite any prior row so the
    # user can flip their vote without leaving stale rating data.
    await db.execute("DELETE FROM feedback WHERE decision_id = ?", (decision_id,))
    await db.execute(
        "INSERT INTO feedback (decision_id, rating, created_at) VALUES (?, ?, ?)",
        (decision_id, payload.rating, int(time.time() * 1000)),
    )
    await db.commit()
    return {"ok": True, "decision_id": decision_id, "rating": payload.rating}


@router.delete("/{decision_id}/feedback")
async def delete_feedback(decision_id: int) -> dict:
    db = get_db()
    async with db.execute("SELECT 1 FROM decisions WHERE id = ?", (decision_id,)) as cur:
        if await cur.fetchone() is None:
            raise HTTPException(404, f"decision {decision_id} not found")
    cur = await db.execute("DELETE FROM feedback WHERE decision_id = ?", (decision_id,))
    await db.commit()
    return {"ok": True, "decision_id": decision_id, "deleted": cur.rowcount > 0}
