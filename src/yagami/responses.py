"""Persistent OpenAI Responses lifecycle and resumable event state."""

from __future__ import annotations

import json
from typing import Any

from .backends.base import Message
from .storage.db import get_db, now_ms


class ResponseNotFoundError(LookupError):
    pass


async def create_response_job(
    *,
    response_id: str,
    project_id: str,
    request_id: str,
    decision_id: int,
    model: str,
    status: str,
    messages: list[Message],
    metadata: dict[str, Any],
    previous_response_id: str | None,
    conversation_id: str | None,
    retention_days: int,
) -> None:
    current = now_ms()
    expires_at = current + retention_days * 86_400_000 if retention_days > 0 else current
    await get_db().execute(
        "INSERT INTO response_jobs(id, project_id, request_id, decision_id, model, status,"
        " previous_response_id, conversation_id, input_json, metadata_json, created_at,"
        " updated_at, expires_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            response_id,
            project_id,
            request_id,
            decision_id,
            model,
            status,
            previous_response_id,
            conversation_id,
            json.dumps(
                [message.model_dump(mode="json") for message in messages],
                sort_keys=True,
                separators=(",", ":"),
            ),
            json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            current,
            current,
            expires_at,
        ),
    )
    await get_db().commit()


async def append_response_event(
    response_id: str, sequence_number: int, event: dict[str, Any]
) -> None:
    await get_db().execute(
        "INSERT INTO response_events(response_id, sequence_number, event_json,"
        " created_at) VALUES(?, ?, ?, ?)"
        " ON CONFLICT(response_id, sequence_number) DO NOTHING",
        (
            response_id,
            sequence_number,
            json.dumps(event, sort_keys=True, separators=(",", ":")),
            now_ms(),
        ),
    )
    await get_db().commit()


async def set_response_status(response_id: str, status: str) -> None:
    await get_db().execute(
        "UPDATE response_jobs SET status=?, updated_at=? WHERE id=?",
        (status, now_ms(), response_id),
    )
    await get_db().commit()


async def complete_response_job(response_id: str, output: dict[str, Any]) -> None:
    await get_db().execute(
        "UPDATE response_jobs SET status='completed', output_json=?, error_json=NULL,"
        " updated_at=? WHERE id=?",
        (json.dumps(output, sort_keys=True, separators=(",", ":")), now_ms(), response_id),
    )
    await get_db().commit()


async def fail_response_job(response_id: str, *, status: str, code: str, message: str) -> None:
    await get_db().execute(
        "UPDATE response_jobs SET status=?, error_json=?, updated_at=? WHERE id=?",
        (
            status,
            json.dumps({"code": code, "message": message}, separators=(",", ":")),
            now_ms(),
            response_id,
        ),
    )
    await get_db().commit()


async def request_response_cancel(response_id: str, project_id: str) -> bool:
    cursor = await get_db().execute(
        "UPDATE response_jobs SET cancel_requested=1, updated_at=?"
        " WHERE id=? AND project_id=? AND status IN ('queued','in_progress')",
        (now_ms(), response_id, project_id),
    )
    await get_db().commit()
    return cursor.rowcount == 1


async def response_cancel_requested(response_id: str) -> bool:
    async with get_db().execute(
        "SELECT cancel_requested FROM response_jobs WHERE id=?", (response_id,)
    ) as cursor:
        row = await cursor.fetchone()
    return bool(row and row["cancel_requested"])


def _decode_job(row: Any) -> dict[str, Any]:
    result = {
        "id": str(row["id"]),
        "object": "response",
        "created_at": int(row["created_at"]) // 1000,
        "status": str(row["status"]),
        "model": str(row["model"]),
        "output": [],
        "metadata": json.loads(str(row["metadata_json"])),
        "previous_response_id": row["previous_response_id"],
        "conversation": row["conversation_id"],
    }
    if row["output_json"]:
        result.update(json.loads(str(row["output_json"])))
    if row["error_json"]:
        result["error"] = json.loads(str(row["error_json"]))
    return result


async def get_response_job(response_id: str, project_id: str) -> dict[str, Any]:
    async with get_db().execute(
        "SELECT * FROM response_jobs WHERE id=? AND project_id=?",
        (response_id, project_id),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        raise ResponseNotFoundError(response_id)
    return _decode_job(row)


async def get_response_context(
    *,
    project_id: str,
    previous_response_id: str | None,
    conversation_id: str | None,
    limit: int = 20,
) -> list[Message]:
    if previous_response_id:
        query = (
            "SELECT input_json, output_json FROM response_jobs"
            " WHERE project_id=? AND status='completed' AND id=?"
            " ORDER BY created_at DESC LIMIT ?"
        )
        params: list[Any] = [project_id, previous_response_id, limit]
    elif conversation_id:
        query = (
            "SELECT input_json, output_json FROM response_jobs"
            " WHERE project_id=? AND status='completed' AND conversation_id=?"
            " ORDER BY created_at DESC LIMIT ?"
        )
        params = [project_id, conversation_id, limit]
    else:
        return []
    rows: list[Any]
    async with get_db().execute(query, tuple(params)) as cursor:
        rows = list(await cursor.fetchall())
    if previous_response_id and not rows:
        raise ResponseNotFoundError(previous_response_id)
    messages: list[Message] = []
    for row in reversed(rows):
        for raw in json.loads(str(row["input_json"])):
            messages.append(Message.model_validate(raw))
        if row["output_json"]:
            output = json.loads(str(row["output_json"]))
            text_parts = [
                content.get("text", "")
                for item in output.get("output", [])
                if item.get("type") == "message"
                for content in item.get("content", [])
                if content.get("type") == "output_text"
            ]
            if text_parts:
                messages.append(Message(role="assistant", content="".join(text_parts)))
    return messages[-256:]


async def list_response_events(
    response_id: str, project_id: str, *, after: int = -1, limit: int = 10_000
) -> list[dict[str, Any]]:
    await get_response_job(response_id, project_id)
    async with get_db().execute(
        "SELECT sequence_number, event_json FROM response_events"
        " WHERE response_id=? AND sequence_number>? ORDER BY sequence_number LIMIT ?",
        (response_id, after, limit),
    ) as cursor:
        return [
            {
                "sequence_number": int(row["sequence_number"]),
                "event": json.loads(str(row["event_json"])),
            }
            async for row in cursor
        ]


async def cleanup_expired_responses() -> int:
    cursor = await get_db().execute(
        "DELETE FROM response_jobs WHERE expires_at IS NOT NULL AND expires_at<=?"
        " AND status NOT IN ('queued','in_progress')",
        (now_ms(),),
    )
    await get_db().commit()
    return max(cursor.rowcount, 0)
