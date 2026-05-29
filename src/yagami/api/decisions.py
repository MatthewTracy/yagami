from __future__ import annotations

from fastapi import APIRouter, Query

from ..telemetry.decisions import list_decisions

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


@router.get("")
async def get_decisions(
    session_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict:
    rows = await list_decisions(session_id=session_id, limit=limit)
    return {"decisions": rows, "count": len(rows)}
