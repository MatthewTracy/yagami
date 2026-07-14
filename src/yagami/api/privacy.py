from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..config import get_config
from ..privacy import cleanup_expired_sessions, data_counts, purge_data, stream_export

router = APIRouter(prefix="/api/privacy", tags=["privacy"])


@router.get("")
async def status() -> dict:
    return {
        "session_retention_days": get_config().privacy.session_retention_days,
        "storage_encryption": {
            "application_managed": False,
            "recommendation": "Enable full-disk encryption (BitLocker, FileVault, or equivalent).",
        },
        "counts": await data_counts(),
    }


@router.post("/cleanup")
async def cleanup() -> dict:
    days = get_config().privacy.session_retention_days
    deleted = await cleanup_expired_sessions(days)
    return {"ok": True, "retention_days": days, "sessions_deleted": deleted}


@router.get("/export")
async def export() -> StreamingResponse:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        stream_export(),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="yagami-export-{stamp}.json"'},
    )


class PurgeBody(BaseModel):
    confirmation: str
    scope: Literal["conversations", "everything"] = "conversations"


@router.delete("/data")
async def purge(body: PurgeBody) -> dict:
    if body.confirmation != "DELETE":
        raise HTTPException(422, "confirmation must be exactly DELETE")
    before = await purge_data(include_knowledge_base=body.scope == "everything")
    return {"ok": True, "scope": body.scope, "deleted": before}
