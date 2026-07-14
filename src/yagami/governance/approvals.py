from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import secrets
from dataclasses import dataclass
from uuid import uuid4

from ..storage.db import get_db, now_ms


class ApprovalError(RuntimeError):
    pass


@dataclass(frozen=True)
class ApprovalGrant:
    id: str
    token: str
    project_id: str
    tools: list[str]
    purpose: str | None
    ticket: str | None
    created_at: int
    expires_at: int


@dataclass(frozen=True)
class ApprovalResolution:
    approved_tools: list[str]
    approval_ids: list[str]


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _matches(patterns: list[str], tool: str) -> bool:
    return any(fnmatch.fnmatchcase(tool, pattern) for pattern in patterns)


class ApprovalStore:
    """One-time, project-bound capabilities for governed tool advertisement."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        project_id: str,
        tools: list[str],
        purpose: str | None,
        ticket: str | None,
        created_by: str | None,
        ttl_seconds: int,
    ) -> ApprovalGrant:
        approval_id = "apr_" + uuid4().hex
        token = "ygma_" + secrets.token_urlsafe(32)
        created_at = now_ms()
        expires_at = created_at + ttl_seconds * 1000
        normalized_tools = list(dict.fromkeys(tool.strip() for tool in tools if tool.strip()))
        await get_db().execute(
            "INSERT INTO tool_approvals(id, project_id, token_hash, tools, purpose, ticket,"
            " created_by, created_at, expires_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                approval_id,
                project_id,
                _token_hash(token),
                json.dumps(normalized_tools, separators=(",", ":")),
                purpose,
                ticket,
                created_by,
                created_at,
                expires_at,
            ),
        )
        await get_db().commit()
        return ApprovalGrant(
            id=approval_id,
            token=token,
            project_id=project_id,
            tools=normalized_tools,
            purpose=purpose,
            ticket=ticket,
            created_at=created_at,
            expires_at=expires_at,
        )

    async def resolve(
        self,
        *,
        project_id: str,
        tokens: list[str],
        requested_tools: list[str],
        purpose: str,
        request_id: str,
        consume: bool,
    ) -> ApprovalResolution:
        approved: set[str] = set()
        approval_ids: list[str] = []
        current = now_ms()
        async with self._lock:
            db = get_db()
            for token in dict.fromkeys(tokens):
                if not token.startswith("ygma_") or len(token) < 32:
                    raise ApprovalError("invalid tool approval token")
                async with db.execute(
                    "SELECT id, tools, purpose, expires_at, consumed_at, revoked_at"
                    " FROM tool_approvals WHERE project_id=? AND token_hash=?",
                    (project_id, _token_hash(token)),
                ) as cursor:
                    row = await cursor.fetchone()
                if row is None:
                    raise ApprovalError("tool approval not found for this project")
                if row["revoked_at"] is not None:
                    raise ApprovalError("tool approval has been revoked")
                if row["consumed_at"] is not None:
                    raise ApprovalError("tool approval has already been consumed")
                if int(row["expires_at"]) < current:
                    raise ApprovalError("tool approval has expired")
                if row["purpose"] is not None and str(row["purpose"]) != purpose:
                    raise ApprovalError("tool approval purpose does not match this request")
                try:
                    patterns = json.loads(str(row["tools"]))
                except (TypeError, ValueError) as exc:
                    raise ApprovalError("stored tool approval is invalid") from exc
                matched = (
                    [tool for tool in requested_tools if _matches(patterns, tool)]
                    if requested_tools
                    else list(patterns)
                )
                if not matched:
                    raise ApprovalError("tool approval does not cover a requested tool")
                approved.update(matched)
                approval_ids.append(str(row["id"]))

            if consume and approval_ids:
                placeholders = ",".join("?" for _ in approval_ids)
                cursor = await db.execute(
                    f"UPDATE tool_approvals SET consumed_at=?, consumed_request_id=?"
                    f" WHERE id IN ({placeholders}) AND consumed_at IS NULL AND revoked_at IS NULL",
                    (current, request_id, *approval_ids),
                )
                if cursor.rowcount != len(approval_ids):
                    await db.rollback()
                    raise ApprovalError("tool approval was consumed concurrently")
                await db.commit()
        return ApprovalResolution(
            approved_tools=sorted(approved),
            approval_ids=approval_ids,
        )

    async def list(self, project_id: str, *, limit: int = 100) -> list[dict]:
        current = now_ms()
        rows: list[dict] = []
        async with get_db().execute(
            "SELECT id, project_id, tools, purpose, ticket, created_by, created_at, expires_at,"
            " consumed_at, consumed_request_id, revoked_at FROM tool_approvals"
            " WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        ) as cursor:
            async for row in cursor:
                record = dict(row)
                record["tools"] = json.loads(record["tools"])
                if record["revoked_at"] is not None:
                    status = "revoked"
                elif record["consumed_at"] is not None:
                    status = "consumed"
                elif int(record["expires_at"]) < current:
                    status = "expired"
                else:
                    status = "active"
                record["status"] = status
                rows.append(record)
        return rows

    async def revoke(self, *, project_id: str, approval_id: str) -> bool:
        cursor = await get_db().execute(
            "UPDATE tool_approvals SET revoked_at=?"
            " WHERE id=? AND project_id=? AND consumed_at IS NULL AND revoked_at IS NULL",
            (now_ms(), approval_id, project_id),
        )
        await get_db().commit()
        return cursor.rowcount == 1

    async def cleanup_expired(self) -> int:
        cursor = await get_db().execute(
            "DELETE FROM tool_approvals WHERE expires_at < ? AND consumed_at IS NULL",
            (now_ms(),),
        )
        await get_db().commit()
        return max(cursor.rowcount, 0)
