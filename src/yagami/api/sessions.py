from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ..chat.session import SessionStore

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

_store_singleton: SessionStore | None = None


def set_store(store: SessionStore) -> None:
    global _store_singleton
    _store_singleton = store


def _store() -> SessionStore:
    if _store_singleton is None:
        raise RuntimeError("SessionStore not registered")
    return _store_singleton


@router.get("")
async def list_(limit: int = Query(default=50, ge=1, le=500)) -> dict:
    rows = await _store().list_sessions(limit=limit)
    return {"sessions": rows, "count": len(rows)}


@router.get("/{session_id}")
async def get_one(session_id: str) -> dict:
    store = _store()
    if not await store.session_exists(session_id):
        raise HTTPException(404, "session not found")
    history = await store.history(session_id)
    return {
        "session_id": session_id,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "images": [image.model_dump(mode="json") for image in m.images or []],
            }
            for m in history
        ],
    }


class RenameBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=120)


@router.patch("/{session_id}")
async def rename(session_id: str, body: RenameBody) -> dict:
    store = _store()
    if not await store.session_exists(session_id):
        raise HTTPException(404, "session not found")
    await store.rename(session_id, body.title)
    return {"ok": True, "session_id": session_id, "title": body.title}


@router.delete("/{session_id}")
async def delete(session_id: str) -> dict:
    store = _store()
    if not await store.session_exists(session_id):
        raise HTTPException(404, "session not found")
    await store.delete(session_id)
    return {"ok": True, "session_id": session_id}
