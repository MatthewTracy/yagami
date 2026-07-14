"""Privacy lifecycle operations for locally persisted Yagami data."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import aiosqlite

from .storage.db import exclusive_db, get_db, now_ms, snapshot_db

_DAY_MS = 24 * 60 * 60 * 1000

_EXPORT_TABLES: tuple[tuple[str, str], ...] = (
    ("sessions", "SELECT * FROM sessions ORDER BY created_at, id"),
    ("messages", "SELECT * FROM messages ORDER BY id"),
    (
        "message_attachments",
        "SELECT id, message_id, media_type, data, created_at FROM message_attachments ORDER BY id",
    ),
    ("decisions", "SELECT * FROM decisions ORDER BY id"),
    ("feedback", "SELECT * FROM feedback ORDER BY id"),
    ("observations", "SELECT * FROM observations ORDER BY id"),
    ("kb_documents", "SELECT * FROM kb_documents ORDER BY id"),
)


async def _data_counts(db: aiosqlite.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in (
        "sessions",
        "messages",
        "message_attachments",
        "decisions",
        "feedback",
        "observations",
        "kb_documents",
    ):
        async with db.execute(f"SELECT COUNT(*) FROM {name}") as cur:
            row = await cur.fetchone()
        counts[name] = int(row[0])
    return counts


async def data_counts() -> dict[str, int]:
    return await _data_counts(get_db())


async def cleanup_expired_sessions(retention_days: int) -> int:
    """Delete stale sessions and every derived observation/vector they own."""
    if retention_days <= 0:
        return 0
    cutoff = now_ms() - retention_days * _DAY_MS
    async with exclusive_db() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM sessions WHERE updated_at < ?", (cutoff,)
        ) as cur:
            count = int((await cur.fetchone())[0])
        if count == 0:
            return 0
        await db.execute(
            """DELETE FROM observations_vec
                 WHERE rowid IN (
                     SELECT id FROM observations
                      WHERE session_id IN (SELECT id FROM sessions WHERE updated_at < ?)
                 )""",
            (cutoff,),
        )
        await db.execute(
            "DELETE FROM observations WHERE session_id IN "
            "(SELECT id FROM sessions WHERE updated_at < ?)",
            (cutoff,),
        )
        await db.execute("DELETE FROM sessions WHERE updated_at < ?", (cutoff,))
        return count


async def purge_data(*, include_knowledge_base: bool) -> dict[str, int]:
    """Delete conversations, memory, and optionally explicitly indexed documents."""
    async with exclusive_db() as db:
        before = await _data_counts(db)
        await db.execute("DELETE FROM observations_vec")
        await db.execute("DELETE FROM observations")
        await db.execute("DELETE FROM sessions")
        if include_knowledge_base:
            await db.execute("DELETE FROM kb_documents_vec")
            await db.execute("DELETE FROM kb_documents")
        after = await _data_counts(db)
        return {name: before[name] - after[name] for name in before}


def _json_record(row: object, *, table: str) -> str:
    record = dict(row)  # type: ignore[arg-type]
    if table == "message_attachments":
        record["data_b64"] = base64.b64encode(record.pop("data")).decode("ascii")
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


async def stream_export() -> AsyncIterator[str]:
    """Stream a complete, human-portable JSON export without buffering the DB."""
    exported_at = datetime.now(UTC).isoformat()
    yield json.dumps({"format": "yagami-export", "version": 1, "exported_at": exported_at})[:-1]
    yield ',"tables":{'
    async with snapshot_db() as db:
        for table_index, (table, query) in enumerate(_EXPORT_TABLES):
            if table_index:
                yield ","
            yield json.dumps(table) + ":["
            first = True
            async with db.execute(query) as cur:
                async for row in cur:
                    if not first:
                        yield ","
                    first = False
                    yield _json_record(row, table=table)
            yield "]"
    yield "}}"
