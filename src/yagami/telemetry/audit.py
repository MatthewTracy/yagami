from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any, Literal, Protocol

import httpx

from ..storage.db import get_db, now_ms

_GENESIS = "0" * 64
log = logging.getLogger("yagami.audit")


class AuditSink(Protocol):
    async def emit(self, event: dict[str, Any]) -> None: ...


class HttpAuditSink:
    """Send content-free audit records to a generic webhook or Splunk HEC."""

    def __init__(
        self,
        url: str,
        *,
        token: str = "",
        sink_format: Literal["json", "splunk_hec"] = "json",
        timeout_seconds: float = 5.0,
    ) -> None:
        if not url.casefold().startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise ValueError("audit sink URL must use HTTPS unless it is loopback")
        self.url = url
        self.token = token
        self.sink_format = sink_format
        self.timeout_seconds = timeout_seconds

    async def emit(self, event: dict[str, Any]) -> None:
        headers = {"Content-Type": "application/json"}
        if self.token:
            prefix = "Splunk" if self.sink_format == "splunk_hec" else "Bearer"
            headers["Authorization"] = f"{prefix} {self.token}"
        body: dict[str, Any] = (
            {"event": event, "source": "yagami"} if self.sink_format == "splunk_hec" else event
        )
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(self.url, headers=headers, json=body)
            response.raise_for_status()


class AuditLedger:
    """Project-scoped append-only hash chains with optional HMAC authentication."""

    def __init__(
        self,
        *,
        key: str = "",
        required: bool = False,
        sink: AuditSink | None = None,
        sink_required: bool = False,
    ) -> None:
        if key and len(key) < 16:
            raise ValueError("YAGAMI_AUDIT_KEY must contain at least 16 characters")
        if required and not key:
            raise ValueError("YAGAMI_AUDIT_REQUIRED requires YAGAMI_AUDIT_KEY")
        self._key = key.encode("utf-8") if key else None
        self.required = required
        if sink_required and sink is None:
            raise ValueError("YAGAMI_AUDIT_SINK_REQUIRED requires YAGAMI_AUDIT_SINK_URL")
        self._sink = sink
        self._sink_required = sink_required
        self.key_id = (
            "hmac-sha256:" + hashlib.sha256(self._key).hexdigest()[:12]
            if self._key is not None
            else "sha256:unkeyed"
        )
        self._lock = asyncio.Lock()

    def _digest(self, value: bytes) -> str:
        if self._key is not None:
            return hmac.new(self._key, value, hashlib.sha256).hexdigest()
        return hashlib.sha256(value).hexdigest()

    def _event_hash(
        self,
        *,
        previous_hash: str,
        created_at: int,
        project_id: str,
        request_id: str | None,
        event_type: str,
        payload_json: str,
    ) -> str:
        canonical = "|".join(
            (
                previous_hash,
                str(created_at),
                project_id,
                request_id or "",
                event_type,
                payload_json,
                self.key_id,
            )
        ).encode("utf-8")
        return self._digest(canonical)

    async def append(
        self,
        *,
        project_id: str,
        event_type: str,
        payload: dict,
        request_id: str | None = None,
    ) -> dict:
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        async with self._lock:
            db = get_db()
            async with db.execute(
                "SELECT event_hash FROM audit_events WHERE project_id=? ORDER BY id DESC LIMIT 1",
                (project_id,),
            ) as cursor:
                row = await cursor.fetchone()
            previous_hash = str(row["event_hash"]) if row else _GENESIS
            created_at = now_ms()
            event_hash = self._event_hash(
                previous_hash=previous_hash,
                created_at=created_at,
                project_id=project_id,
                request_id=request_id,
                event_type=event_type,
                payload_json=payload_json,
            )
            cursor = await db.execute(
                "INSERT INTO audit_events(created_at, project_id, request_id, event_type, payload,"
                " previous_hash, event_hash, key_id) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    created_at,
                    project_id,
                    request_id,
                    event_type,
                    payload_json,
                    previous_hash,
                    event_hash,
                    self.key_id,
                ),
            )
            await db.commit()
            record = {
                "id": int(cursor.lastrowid or 0),
                "created_at": created_at,
                "event_hash": event_hash,
                "previous_hash": previous_hash,
                "key_id": self.key_id,
                "project_id": project_id,
                "request_id": request_id,
                "event_type": event_type,
                "payload": payload,
            }
        if self._sink is not None:
            try:
                await self._sink.emit(record)
            except Exception:
                if self._sink_required:
                    raise
                log.exception("optional audit sink delivery failed")
        return record

    async def verify(self, project_id: str) -> dict:
        db = get_db()
        async with db.execute(
            "SELECT id, created_at, project_id, request_id, event_type, payload, previous_hash,"
            " event_hash, key_id FROM audit_events WHERE project_id=? ORDER BY id",
            (project_id,),
        ) as cursor:
            rows = list(await cursor.fetchall())
        expected_previous = _GENESIS
        for index, row in enumerate(rows):
            if row["previous_hash"] != expected_previous:
                return {
                    "valid": False,
                    "events": len(rows),
                    "invalid_event_id": int(row["id"]),
                    "reason": "previous hash mismatch",
                }
            if row["key_id"] != self.key_id:
                return {
                    "valid": False,
                    "events": len(rows),
                    "invalid_event_id": int(row["id"]),
                    "reason": "audit key ID differs from active key",
                }
            calculated = self._event_hash(
                previous_hash=str(row["previous_hash"]),
                created_at=int(row["created_at"]),
                project_id=str(row["project_id"]),
                request_id=row["request_id"],
                event_type=str(row["event_type"]),
                payload_json=str(row["payload"]),
            )
            if not hmac.compare_digest(calculated, str(row["event_hash"])):
                return {
                    "valid": False,
                    "events": len(rows),
                    "invalid_event_id": int(row["id"]),
                    "reason": f"event hash mismatch at position {index}",
                }
            expected_previous = str(row["event_hash"])
        return {
            "valid": True,
            "events": len(rows),
            "head": expected_previous,
            "key_id": self.key_id,
        }

    async def export_ndjson(self, project_id: str, *, limit: int = 100_000) -> str:
        db = get_db()
        lines: list[str] = []
        async with db.execute(
            "SELECT id, created_at, project_id, request_id, event_type, payload, previous_hash,"
            " event_hash, key_id FROM audit_events WHERE project_id=? ORDER BY id LIMIT ?",
            (project_id, limit),
        ) as cursor:
            async for row in cursor:
                record = dict(row)
                try:
                    record["payload"] = json.loads(record["payload"])
                except (TypeError, ValueError):
                    pass
                lines.append(json.dumps(record, sort_keys=True, separators=(",", ":")))
        return "\n".join(lines) + ("\n" if lines else "")
