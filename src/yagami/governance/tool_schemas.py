"""Content-free tool-schema pinning and drift quarantine."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass

from ..storage.db import get_db, now_ms


@dataclass(frozen=True)
class ToolSchemaCheck:
    tool_name: str
    schema_hash: str
    status: str

    def summary(self) -> dict[str, str]:
        return {
            "tool_name": self.tool_name,
            "schema_hash": self.schema_hash,
            "status": self.status,
        }


def _tool_identity(tool: dict) -> tuple[str, str]:
    function = tool.get("function")
    if not isinstance(function, dict) or not isinstance(function.get("name"), str):
        raise ValueError("each tool must contain a named function schema")
    name = function["name"].strip()
    if not name or len(name) > 256:
        raise ValueError("tool names must contain 1 to 256 characters")
    canonical = json.dumps(tool, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return name, "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ToolSchemaRegistry:
    """Pins first-used schemas per project and quarantines later drift."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def inspect(
        self,
        *,
        project_id: str,
        tools: list[dict],
        pin_missing: bool,
    ) -> list[ToolSchemaCheck]:
        checks: list[ToolSchemaCheck] = []
        async with self._lock:
            db = get_db()
            for tool in tools:
                name, schema_hash = _tool_identity(tool)
                async with db.execute(
                    "SELECT pinned_hash FROM tool_schema_pins WHERE project_id=? AND tool_name=?",
                    (project_id, name),
                ) as cursor:
                    row = await cursor.fetchone()
                current = now_ms()
                if row is None:
                    status = "pinned" if pin_missing else "unpinned"
                    if pin_missing:
                        await db.execute(
                            "INSERT INTO tool_schema_pins(project_id, tool_name, pinned_hash,"
                            " first_seen_at, last_seen_at) VALUES(?, ?, ?, ?, ?)",
                            (project_id, name, schema_hash, current, current),
                        )
                elif str(row["pinned_hash"]) == schema_hash:
                    status = "matched"
                    await db.execute(
                        "UPDATE tool_schema_pins SET last_seen_at=?, pending_hash=NULL"
                        " WHERE project_id=? AND tool_name=?",
                        (current, project_id, name),
                    )
                else:
                    status = "drift"
                    await db.execute(
                        "UPDATE tool_schema_pins SET last_seen_at=?, pending_hash=?"
                        " WHERE project_id=? AND tool_name=?",
                        (current, schema_hash, project_id, name),
                    )
                checks.append(ToolSchemaCheck(name, schema_hash, status))
            await db.commit()
        return checks

    async def approve_pending(
        self,
        *,
        project_id: str,
        tool_name: str,
        approved_by: str | None,
    ) -> bool:
        current = now_ms()
        cursor = await get_db().execute(
            "UPDATE tool_schema_pins SET pinned_hash=pending_hash, pending_hash=NULL,"
            " approved_at=?, approved_by=? WHERE project_id=? AND tool_name=?"
            " AND pending_hash IS NOT NULL",
            (current, approved_by, project_id, tool_name),
        )
        await get_db().commit()
        return cursor.rowcount == 1

    async def list(self, *, project_id: str, limit: int = 500) -> list[dict]:
        async with get_db().execute(
            "SELECT project_id, tool_name, pinned_hash, pending_hash, first_seen_at,"
            " last_seen_at, approved_at, approved_by FROM tool_schema_pins"
            " WHERE project_id=? ORDER BY tool_name LIMIT ?",
            (project_id, limit),
        ) as cursor:
            return [dict(row) async for row in cursor]
