"""Folder-based document knowledge base.

Separate corpus from cross-session chat memory (memory/store.py +
observations table): this is reference material the user explicitly chose
to index (`POST /api/kb/index`), not something the classifier tagged from a
conversation. Reuses the Embedder class and the sqlite-vec storage pattern
from that module, but with its own `kb_documents` / `kb_documents_vec` /
`kb_documents_fts` tables (see migrations/007_kb_documents.sql) - no
session_id, no sensitivity/TTL, and no per-chunk cap (chunker.MAX_CHUNKS
exists to bound one chat turn, not a whole document corpus).

Indexing embeds synchronously within the request instead of queuing for the
background worker (memory/worker.py drains the *observations* table only).
That's a deliberate v1 tradeoff: chat turns need a background worker because
they're on the hot path of every message; folder indexing is an explicit,
infrequent action where blocking the API call until embedding finishes is
acceptable. A large folder will take a while - the caller sees it as
request latency, not a silent background failure.
"""

from __future__ import annotations

import logging
import struct
import time
from pathlib import Path

from ..ingest.extract import extract
from ..storage.db import get_db
from .chunker import chunk
from .embedder import Embedder

log = logging.getLogger("yagami.memory.documents")

_SUPPORTED_SUFFIXES = {".pdf", ".md", ".markdown", ".txt", ".log"}
_MAX_CHARS_PER_FILE = 5_000_000
# Bounded, not unlimited - a few thousand chunks (~a few million tokens) is
# enough headroom for "a folder of docs" while still capping worst-case
# memory/DB writes from one pathological file.
_MAX_CHUNKS_PER_FILE = 2000


def _vec_blob(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _iter_files(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in _SUPPORTED_SUFFIXES
    )


async def _replace_document(source_path: str, chunks: list[str], *, embedder: Embedder) -> int:
    """Delete any existing rows for `source_path`, then insert + embed the
    new chunks. Returns the number of chunks written."""
    db = get_db()
    async with db.execute(
        "SELECT id FROM kb_documents WHERE source_path = ?", (source_path,)
    ) as cur:
        old_ids = [int(r[0]) async for r in cur]
    if old_ids:
        qmarks = ",".join("?" * len(old_ids))
        await db.execute(f"DELETE FROM kb_documents_vec WHERE rowid IN ({qmarks})", old_ids)
        await db.execute(f"DELETE FROM kb_documents WHERE id IN ({qmarks})", old_ids)

    now = int(time.time() * 1000)
    for i, text in enumerate(chunks):
        cur = await db.execute(
            "INSERT INTO kb_documents (source_path, chunk_index, text, created_at, embedding_status)"
            " VALUES (?, ?, ?, ?, 'pending')",
            (source_path, i, text, now),
        )
        row_id = cur.lastrowid
        vec = await embedder.embed(text)
        if vec is not None:
            try:
                await db.execute(
                    "INSERT INTO kb_documents_vec(rowid, embedding) VALUES (?, ?)",
                    (row_id, _vec_blob(vec)),
                )
                await db.execute(
                    "UPDATE kb_documents SET embedding_status='ready' WHERE id=?", (row_id,)
                )
            except Exception as exc:  # noqa: BLE001 - vec insert failure shouldn't crash indexing
                log.warning("vec insert failed for kb_document %s: %s", row_id, exc)
                await db.execute(
                    "UPDATE kb_documents SET embedding_status='failed' WHERE id=?", (row_id,)
                )
        else:
            await db.execute(
                "UPDATE kb_documents SET embedding_status='failed' WHERE id=?", (row_id,)
            )
    await db.commit()
    return len(chunks)


async def index_folder(folder: Path, *, embedder: Embedder) -> dict:
    """Index every supported file (.pdf/.md/.txt/.log) under `folder`,
    recursively. Re-running is idempotent per file - existing chunks for a
    source_path are replaced, not duplicated. Returns a summary dict."""
    files = _iter_files(folder)
    files_indexed = 0
    files_skipped = 0
    chunks_written = 0
    for path in files:
        try:
            blob = path.read_bytes()
        except OSError as exc:
            log.warning("failed to read %s: %s", path, exc)
            files_skipped += 1
            continue
        doc = extract(filename=path.name, mime="", blob=blob, max_chars=_MAX_CHARS_PER_FILE)
        if doc.error or not doc.text:
            files_skipped += 1
            continue
        chunks = chunk(doc.text, max_chunks=_MAX_CHUNKS_PER_FILE)
        n = await _replace_document(str(path), chunks, embedder=embedder)
        files_indexed += 1
        chunks_written += n
    return {
        "files_indexed": files_indexed,
        "files_skipped": files_skipped,
        "chunks_written": chunks_written,
    }


async def list_sources() -> list[dict]:
    """One row per indexed file: source_path, chunk count, most recent
    created_at. Used by GET /api/kb."""
    db = get_db()
    async with db.execute(
        "SELECT source_path, COUNT(*), MAX(created_at) FROM kb_documents GROUP BY source_path"
        " ORDER BY source_path"
    ) as cur:
        rows = await cur.fetchall()
    return [{"source_path": r[0], "chunks": int(r[1]), "indexed_at": int(r[2])} for r in rows]


async def delete_source(source_path: str) -> int:
    """Remove every chunk for one indexed file. Returns the number deleted."""
    db = get_db()
    async with db.execute(
        "SELECT id FROM kb_documents WHERE source_path = ?", (source_path,)
    ) as cur:
        ids = [int(r[0]) async for r in cur]
    if not ids:
        return 0
    qmarks = ",".join("?" * len(ids))
    await db.execute(f"DELETE FROM kb_documents_vec WHERE rowid IN ({qmarks})", ids)
    await db.execute(f"DELETE FROM kb_documents WHERE id IN ({qmarks})", ids)
    await db.commit()
    return len(ids)


async def search(query: str, *, embedder: Embedder, k: int = 5) -> list[dict]:
    """Vector search over kb_documents, with an FTS5 backfill - same
    two-stage pattern as memory/retriever.py's Retriever.fetch(), kept as a
    separate implementation since it's a different table shape (source_path
    instead of session_id, no sensitivity field to filter on)."""
    query = query.strip()
    if not query:
        return []
    db = get_db()
    hits: list[dict] = []
    vec = await embedder.embed(query)
    if vec is not None:
        try:
            async with db.execute(
                """
                SELECT d.id, d.source_path, d.chunk_index, d.text, v.distance
                FROM kb_documents_vec v
                JOIN kb_documents d ON d.id = v.rowid
                WHERE v.embedding MATCH ? AND k = ?
                  AND d.embedding_status = 'ready'
                ORDER BY v.distance ASC
                LIMIT ?
                """,
                (_vec_blob(vec), k, k),
            ) as cur:
                rows = await cur.fetchall()
            hits = [
                {
                    "id": int(r[0]),
                    "source_path": str(r[1]),
                    "chunk_index": int(r[2]),
                    "text": str(r[3]),
                    "distance": float(r[4]),
                }
                for r in rows
            ]
        except Exception as exc:  # noqa: BLE001 - vec query failure shouldn't break search
            log.warning("kb vec search failed: %s; falling back to FTS only", exc)

    if len(hits) < k:
        seen = {h["id"] for h in hits}
        cleaned = query.replace('"', "").strip()
        if cleaned:
            try:
                async with db.execute(
                    """
                    SELECT d.id, d.source_path, d.chunk_index, d.text
                    FROM kb_documents_fts f
                    JOIN kb_documents d ON d.id = f.rowid
                    WHERE f.text MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (cleaned, k - len(hits)),
                ) as cur:
                    rows = await cur.fetchall()
                for r in rows:
                    if int(r[0]) not in seen:
                        hits.append(
                            {
                                "id": int(r[0]),
                                "source_path": str(r[1]),
                                "chunk_index": int(r[2]),
                                "text": str(r[3]),
                                "distance": None,
                            }
                        )
            except Exception as exc:  # noqa: BLE001 - FTS MATCH can throw on weird tokens
                log.warning("kb fts search failed: %s", exc)

    return hits[:k]
