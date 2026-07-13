"""Folder-based document knowledge base admin: index a directory, list
what's indexed, and remove a source. See memory/documents.py for the
storage/search implementation and skills/kb_recall.py for how the LLM
queries what's indexed here.

Trust note: like `PUT /api/config` (arbitrary TOML rewrite) and the file
ingest endpoint (arbitrary uploaded file content), this lets anyone who can
reach the local API make the server read files from disk - `index_folder`
takes a path and walks it recursively. That's consistent with Yagami's
existing single-user local-app trust model (bind to 127.0.0.1, as the
`yagami` CLI already defaults to), not a new category of exposure, but it's
worth stating plainly: don't expose this port beyond localhost.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import get_config
from ..memory import documents
from ..memory.embedder import Embedder

router = APIRouter(prefix="/api/kb", tags=["kb"])


class IndexRequest(BaseModel):
    path: str


@router.post("/index")
async def index_folder(req: IndexRequest) -> dict:
    folder = Path(req.path).expanduser()
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(400, f"not a directory: {folder}")
    cfg = get_config()
    embedder = Embedder(url=cfg.ollama.url, model=cfg.memory.embedding_model)
    summary = await documents.index_folder(folder, embedder=embedder)
    return {"ok": True, "folder": str(folder), **summary}


@router.get("")
async def list_indexed() -> dict:
    sources = await documents.list_sources()
    return {"sources": sources, "count": len(sources)}


@router.delete("/source")
async def delete_source(path: str) -> dict:
    n = await documents.delete_source(path)
    if n == 0:
        raise HTTPException(404, f"no indexed chunks for {path!r}")
    return {"ok": True, "source_path": path, "deleted_chunks": n}
