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

import asyncio
import os
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from ..config import get_config, get_settings
from ..memory import documents
from ..memory.embedder import Embedder

router = APIRouter(prefix="/api/kb", tags=["kb"])


class IndexRequest(BaseModel):
    path: str
    wait: bool = False
    prune_missing: bool = True


_jobs: dict[str, dict] = {}
_tasks: set[asyncio.Task] = set()
_MAX_RETAINED_JOBS = 100


def _configured_roots() -> list[Path]:
    configured = get_settings().kb_roots.strip()
    values = [value for value in configured.split(os.pathsep) if value.strip()]
    if not values:
        values = [str(Path.home())]
    roots: list[Path] = []
    for value in values:
        try:
            roots.append(Path(value).expanduser().resolve(strict=True))
        except OSError:
            continue
    return roots


def _resolve_allowed_folder(raw_path: str) -> Path:
    candidate = os.path.realpath(os.path.expanduser(raw_path))
    for root in _configured_roots():
        trusted_root = os.path.realpath(root)
        candidate_key = os.path.normcase(candidate)
        trusted_key = os.path.normcase(trusted_root)
        trusted_prefix = os.path.join(trusted_key, "")
        if candidate_key != trusted_key and not candidate_key.startswith(trusted_prefix):
            continue
        relative = os.path.relpath(candidate, trusted_root)
        folder = root
        if relative == os.curdir:
            return folder
        for component in relative.split(os.sep):
            if component in {"", os.curdir, os.pardir}:
                raise HTTPException(403, "invalid directory component")
            try:
                child = next((entry for entry in folder.iterdir() if entry.name == component), None)
                if child is None:
                    raise HTTPException(400, "not a readable directory")
                folder = child.resolve(strict=True)
            except OSError as exc:
                raise HTTPException(400, "not a readable directory") from exc
            if not folder.is_dir():
                raise HTTPException(400, "not a readable directory")
            if folder != root and not folder.is_relative_to(root):
                raise HTTPException(403, "directory is outside YAGAMI_KB_ROOTS")
        return folder
    raise HTTPException(403, "directory is outside YAGAMI_KB_ROOTS")


async def _run_index(job_id: str, folder: Path, *, prune_missing: bool) -> dict:
    job = _jobs[job_id]
    job["status"] = "running"
    cfg = get_config()
    embedder = Embedder(url=cfg.ollama.url, model=cfg.memory.embedding_model)
    try:
        summary = await documents.index_folder(
            folder,
            embedder=embedder,
            prune_missing=prune_missing,
        )
        job.update(status="completed", result={"folder": str(folder), **summary})
    except asyncio.CancelledError:
        job.update(status="cancelled")
        raise
    except Exception:  # noqa: BLE001 - job exposes a stable error, details stay in logs
        job.update(status="failed", error="indexing failed; inspect server logs")
    finally:
        await embedder.close()
    return job


def _retain_recent_jobs() -> None:
    completed = [
        key for key, value in _jobs.items() if value["status"] not in {"queued", "running"}
    ]
    for job_id in completed[:-_MAX_RETAINED_JOBS]:
        _jobs.pop(job_id, None)


@router.post("/index")
async def index_folder(req: IndexRequest, response: Response) -> dict:
    folder = _resolve_allowed_folder(req.path)
    job_id = "idx_" + uuid4().hex
    _jobs[job_id] = {"job_id": job_id, "status": "queued"}
    if req.wait:
        job = await _run_index(job_id, folder, prune_missing=req.prune_missing)
        if job["status"] != "completed":
            raise HTTPException(500, job.get("error", "indexing failed"))
        return {"ok": True, "job_id": job_id, **job["result"]}

    task = asyncio.create_task(
        _run_index(job_id, folder, prune_missing=req.prune_missing),
        name=f"kb.index.{job_id}",
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    _retain_recent_jobs()
    response.status_code = 202
    return {"ok": True, "job_id": job_id, "status": "queued"}


@router.get("/jobs/{job_id}")
async def get_index_job(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "index job not found")
    return dict(job)


async def shutdown_jobs() -> None:
    tasks = list(_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()


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
