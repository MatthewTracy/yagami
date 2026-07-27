"""Privacy lifecycle operations for locally persisted Yagami data."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from .storage.db import (
    DatabaseConnection,
    DatabaseRow,
    exclusive_db,
    get_db,
    now_ms,
    snapshot_db,
)

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
    ("audit_events", "SELECT * FROM audit_events ORDER BY id"),
    (
        "tool_approvals",
        "SELECT id, project_id, tools, purpose, ticket, created_by, created_at, expires_at,"
        " consumed_at, consumed_request_id, revoked_at FROM tool_approvals ORDER BY created_at, id",
    ),
)


async def _data_counts(db: DatabaseConnection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in (
        "sessions",
        "messages",
        "message_attachments",
        "decisions",
        "feedback",
        "observations",
        "kb_documents",
        "privacy_tokens",
        "mcp_oauth_states",
        "mcp_oauth_credentials",
        "audit_events",
        "tool_approvals",
    ):
        async with db.execute(  # noqa: S608 -- name comes from the fixed table tuple above
            f"SELECT COUNT(*) FROM {name}"  # noqa: S608 -- fixed internal table tuple
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise RuntimeError(f"count query for {name} returned no row")
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
            row = await cur.fetchone()
            if row is None:
                raise RuntimeError("session count query returned no row")
            count = int(row[0])
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


async def cleanup_policy_retention(*, current_time_ms: int | None = None) -> int:
    """Delete decision records after the retention period in their policy receipt.

    Rows created before policy receipts existed are left to the installation's
    session-retention setting. Memory and tokenized transformations have their
    own TTL cleanup paths.
    """
    current = current_time_ms if current_time_ms is not None else now_ms()
    expired: list[int] = []
    async with exclusive_db() as db:
        async with db.execute(
            "SELECT id, created_at, policy_decision FROM decisions"
            " WHERE policy_decision IS NOT NULL"
        ) as cursor:
            async for row in cursor:
                try:
                    receipt = json.loads(row["policy_decision"])
                    retention_days = int(receipt["retention_days"])
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                expires_at = int(row["created_at"]) + retention_days * _DAY_MS
                if expires_at <= current:
                    expired.append(int(row["id"]))
        if not expired:
            return 0
        placeholders = ",".join("?" for _ in expired)
        await db.execute(
            f"DELETE FROM decisions WHERE id IN ({placeholders})",  # noqa: S608 -- generated placeholders only
            expired,
        )
    return len(expired)


async def purge_data(*, include_knowledge_base: bool) -> dict[str, int]:
    """Delete conversations, memory, and optionally explicitly indexed documents."""
    async with exclusive_db() as db:
        before = await _data_counts(db)
        await db.execute("DELETE FROM observations_vec")
        await db.execute("DELETE FROM observations")
        await db.execute("DELETE FROM privacy_tokens")
        await db.execute("DELETE FROM mcp_oauth_states")
        await db.execute("DELETE FROM mcp_oauth_credentials")
        await db.execute("DELETE FROM sessions")
        if include_knowledge_base:
            await db.execute("DELETE FROM kb_documents_vec")
            await db.execute("DELETE FROM kb_documents")
        after = await _data_counts(db)
        return {name: before[name] - after[name] for name in before}


def _json_record(row: DatabaseRow, *, table: str) -> str:
    record = {key: row[key] for key in row.keys()}
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
