from __future__ import annotations

import base64

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..ingest.extract import extract

router = APIRouter(prefix="/api/ingest", tags=["ingest"])

_MAX_BYTES = 20 * 1024 * 1024  # 20 MB hard cap
_IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


@router.post("")
async def ingest(file: UploadFile = File(...)) -> dict:
    blob = await file.read()
    if len(blob) > _MAX_BYTES:
        raise HTTPException(413, f"file too large ({len(blob)} > {_MAX_BYTES} bytes)")

    mime = file.content_type or ""
    fname = file.filename or "upload"
    low = fname.lower()
    if mime in _IMAGE_MIMES or low.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
        # Normalize mime if browser was vague
        if low.endswith(".png"):
            mime = "image/png"
        elif low.endswith((".jpg", ".jpeg")):
            mime = "image/jpeg"
        elif low.endswith(".gif"):
            mime = "image/gif"
        elif low.endswith(".webp"):
            mime = "image/webp"
        return {
            "kind": "image",
            "filename": fname,
            "media_type": mime,
            "bytes": len(blob),
            "data_b64": base64.b64encode(blob).decode("ascii"),
        }

    doc = extract(filename=fname, mime=mime, blob=blob)
    if doc.error:
        raise HTTPException(415, doc.error)
    return {
        "kind": "document",
        "filename": doc.filename,
        "mime": doc.mime,
        "text": doc.text,
        "truncated": doc.truncated,
        "bytes": len(blob),
        "chars": len(doc.text),
    }
