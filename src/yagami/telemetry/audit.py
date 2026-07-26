from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
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
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, follow_redirects=False
        ) as client:
            response = await client.post(self.url, headers=headers, json=body)
            response.raise_for_status()


def _key_id(key: bytes | None) -> str:
    return (
        "hmac-sha256:"
        + hmac.new(key, b"yagami-audit-key-id-v1", hashlib.sha256).hexdigest()[:12]
        if key is not None
        else "sha256:unkeyed"
    )


def _error_code(exc: BaseException) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", type(exc).__name__).casefold()[:64]


class AuditLedger:
    """Tamper-evident key epochs plus a durable external-delivery outbox."""

    def __init__(
        self,
        *,
        key: str = "",
        previous_keys: list[str] | None = None,
        required: bool = False,
        sink: AuditSink | None = None,
        sink_required: bool = False,
        outbox_max_pending: int = 100_000,
        outbox_max_attempts: int = 12,
    ) -> None:
        keys = [value for value in [key, *(previous_keys or [])] if value]
        if any(len(value) < 16 for value in keys):
            raise ValueError("YAGAMI_AUDIT_KEY values must contain at least 16 characters")
        if required and not key:
            raise ValueError("YAGAMI_AUDIT_REQUIRED requires YAGAMI_AUDIT_KEY")
        if sink_required and sink is None:
            raise ValueError("YAGAMI_AUDIT_SINK_REQUIRED requires YAGAMI_AUDIT_SINK_URL")
        self._key = key.encode("utf-8") if key else None
        self.key_id = _key_id(self._key)
        self._verification_keys: dict[str, bytes | None] = {
            _key_id(value.encode("utf-8")): value.encode("utf-8") for value in keys
        }
        if not keys:
            self._verification_keys[self.key_id] = None
        self.required = required
        self._sink = sink
        self._sink_required = sink_required
        self._outbox_max_pending = outbox_max_pending
        self._outbox_max_attempts = outbox_max_attempts
        self._lock = asyncio.Lock()
        self._delivery_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None

    def _digest(self, value: bytes, *, key_id: str) -> str:
        if key_id not in self._verification_keys:
            raise KeyError(key_id)
        key = self._verification_keys[key_id]
        if key is not None:
            return hmac.new(key, value, hashlib.sha256).hexdigest()
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
        key_id: str,
    ) -> str:
        canonical = "|".join(
            (
                previous_hash,
                str(created_at),
                project_id,
                request_id or "",
                event_type,
                payload_json,
                key_id,
            )
        ).encode("utf-8")
        return self._digest(canonical, key_id=key_id)

    def start(self) -> None:
        if self._sink is not None and self._worker is None:
            self._worker = asyncio.create_task(self._delivery_loop())

    async def stop(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def _delivery_loop(self) -> None:
        while True:
            try:
                delivered = await self.deliver_pending(limit=100)
                if delivered == 0:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=5.0)
                    except TimeoutError:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - durable rows survive worker failures
                log.exception("audit outbox worker failed")
                await asyncio.sleep(1.0)

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
            if self._sink_required:
                async with db.execute(
                    "SELECT COUNT(*) AS count FROM audit_outbox"
                    " WHERE delivered_at IS NULL AND dead_lettered_at IS NULL"
                ) as cursor:
                    pending = await cursor.fetchone()
                if pending is not None and int(pending["count"]) >= self._outbox_max_pending:
                    raise RuntimeError("required audit outbox backpressure limit reached")
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
                key_id=self.key_id,
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
            event_id = int(cursor.lastrowid or 0)
            await db.execute(
                "INSERT INTO audit_key_epochs(key_id, first_used_at, last_used_at)"
                " VALUES(?, ?, ?) ON CONFLICT(key_id) DO UPDATE SET last_used_at=excluded.last_used_at",
                (self.key_id, created_at, created_at),
            )
            record = {
                "id": event_id,
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
                await db.execute(
                    "INSERT INTO audit_outbox(audit_event_id, project_id, event_json, attempts,"
                    " next_attempt_at, created_at) VALUES(?, ?, ?, 0, ?, ?)",
                    (
                        event_id,
                        project_id,
                        json.dumps(record, sort_keys=True, separators=(",", ":")),
                        created_at,
                        created_at,
                    ),
                )
            await db.commit()
        if self._sink is not None:
            self._wake.set()
        return record

    async def deliver_pending(self, *, limit: int = 100) -> int:
        if self._sink is None:
            return 0
        delivered = 0
        async with self._delivery_lock:
            db = get_db()
            async with db.execute(
                "SELECT id, event_json, attempts FROM audit_outbox"
                " WHERE delivered_at IS NULL AND dead_lettered_at IS NULL"
                " AND next_attempt_at<=? ORDER BY id LIMIT ?",
                (now_ms(), limit),
            ) as cursor:
                rows = list(await cursor.fetchall())
            for row in rows:
                outbox_id = int(row["id"])
                attempts = int(row["attempts"])
                try:
                    event = json.loads(str(row["event_json"]))
                    await self._sink.emit(event)
                except Exception as exc:  # noqa: BLE001 - convert to durable state
                    attempt_count = attempts + 1
                    dead_at = now_ms() if attempt_count >= self._outbox_max_attempts else None
                    backoff_ms = min(3_600_000, 1_000 * (2 ** min(attempt_count, 12)))
                    await db.execute(
                        "UPDATE audit_outbox SET attempts=?, next_attempt_at=?,"
                        " last_error_code=?, dead_lettered_at=? WHERE id=?",
                        (
                            attempt_count,
                            now_ms() + backoff_ms,
                            _error_code(exc),
                            dead_at,
                            outbox_id,
                        ),
                    )
                    log.warning(
                        "audit outbox delivery failed id=%d code=%s",
                        outbox_id,
                        _error_code(exc),
                    )
                else:
                    await db.execute(
                        "UPDATE audit_outbox SET delivered_at=?, last_error_code=NULL WHERE id=?",
                        (now_ms(), outbox_id),
                    )
                    delivered += 1
                await db.commit()
        return delivered

    async def outbox_status(self) -> dict[str, int]:
        async with get_db().execute(
            "SELECT"
            " SUM(CASE WHEN delivered_at IS NULL AND dead_lettered_at IS NULL THEN 1 ELSE 0 END)"
            " AS pending,"
            " SUM(CASE WHEN delivered_at IS NOT NULL THEN 1 ELSE 0 END) AS delivered,"
            " SUM(CASE WHEN dead_lettered_at IS NOT NULL THEN 1 ELSE 0 END) AS dead_lettered"
            " FROM audit_outbox"
        ) as cursor:
            row = await cursor.fetchone()
        return {
            "pending": int(row["pending"] or 0) if row else 0,
            "delivered": int(row["delivered"] or 0) if row else 0,
            "dead_lettered": int(row["dead_lettered"] or 0) if row else 0,
        }

    async def replay_dead_letters(self, *, project_id: str | None = None) -> int:
        query = (
            "UPDATE audit_outbox SET dead_lettered_at=NULL, attempts=0,"
            " next_attempt_at=?, last_error_code=NULL WHERE dead_lettered_at IS NOT NULL"
        )
        params: tuple[Any, ...] = (now_ms(),)
        if project_id is not None:
            query += " AND project_id=?"
            params = (now_ms(), project_id)
        cursor = await get_db().execute(query, params)
        await get_db().commit()
        if cursor.rowcount:
            self._wake.set()
        return max(cursor.rowcount, 0)

    async def verify(self, project_id: str) -> dict:
        async with get_db().execute(
            "SELECT id, created_at, project_id, request_id, event_type, payload, previous_hash,"
            " event_hash, key_id FROM audit_events WHERE project_id=? ORDER BY id",
            (project_id,),
        ) as cursor:
            rows = list(await cursor.fetchall())
        expected_previous = _GENESIS
        epochs: list[str] = []
        for index, row in enumerate(rows):
            row_key_id = str(row["key_id"])
            if row["previous_hash"] != expected_previous:
                return {
                    "valid": False,
                    "events": len(rows),
                    "invalid_event_id": int(row["id"]),
                    "reason": "previous hash mismatch",
                }
            if row_key_id not in self._verification_keys:
                return {
                    "valid": False,
                    "events": len(rows),
                    "invalid_event_id": int(row["id"]),
                    "reason": "audit verification key unavailable",
                    "missing_key_id": row_key_id,
                }
            calculated = self._event_hash(
                previous_hash=str(row["previous_hash"]),
                created_at=int(row["created_at"]),
                project_id=str(row["project_id"]),
                request_id=row["request_id"],
                event_type=str(row["event_type"]),
                payload_json=str(row["payload"]),
                key_id=row_key_id,
            )
            if not hmac.compare_digest(calculated, str(row["event_hash"])):
                return {
                    "valid": False,
                    "events": len(rows),
                    "invalid_event_id": int(row["id"]),
                    "reason": f"event hash mismatch at position {index}",
                }
            if not epochs or epochs[-1] != row_key_id:
                epochs.append(row_key_id)
            expected_previous = str(row["event_hash"])
        return {
            "valid": True,
            "events": len(rows),
            "head": expected_previous,
            "key_id": self.key_id,
            "key_epochs": epochs,
        }

    async def export_ndjson(self, project_id: str, *, limit: int = 100_000) -> str:
        lines: list[str] = []
        async with get_db().execute(
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
