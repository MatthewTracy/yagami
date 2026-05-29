from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from ..storage.db import get_db, now_ms

log = logging.getLogger("yagami.decisions")

_PHI_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b\d{16}\b"),
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    re.compile(r"\b\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
]


def scrub(text: str) -> str:
    out = text
    for pat in _PHI_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


async def persist_decision(
    *,
    session_id: str,
    user_text: str,
    decision: dict,
    timings: dict | None = None,
) -> int:
    preview = scrub(user_text)[:280]
    classification = decision.get("classification", {})
    source = classification.get("source", "unknown") if isinstance(classification, dict) else "unknown"
    t = timings or {}
    db = get_db()
    cur = await db.execute(
        "INSERT INTO decisions("
        " session_id, created_at, backend, is_local, reason, classification, scrubbed_preview,"
        " source, t_classify_ms, t_first_token_ms, t_total_ms"
        ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            now_ms(),
            decision["backend"],
            1 if decision["is_local"] else 0,
            decision["reason"],
            json.dumps(classification),
            preview,
            source,
            t.get("classify_ms"),
            t.get("first_token_ms"),
            t.get("total_ms"),
        ),
    )
    await db.commit()
    return cur.lastrowid or 0


async def update_decision_timings(decision_id: int, *, first_token_ms: int | None = None, total_ms: int | None = None) -> None:
    if decision_id <= 0:
        return
    db = get_db()
    await db.execute(
        "UPDATE decisions SET t_first_token_ms = COALESCE(?, t_first_token_ms),"
        " t_total_ms = COALESCE(?, t_total_ms) WHERE id = ?",
        (first_token_ms, total_ms, decision_id),
    )
    await db.commit()


async def list_decisions(*, session_id: str | None = None, limit: int = 100) -> list[dict]:
    db = get_db()
    cols = (
        "id, session_id, created_at, backend, is_local, reason, classification,"
        " scrubbed_preview, source, t_classify_ms, t_first_token_ms, t_total_ms"
    )
    if session_id:
        sql = f"SELECT {cols} FROM decisions WHERE session_id=? ORDER BY id DESC LIMIT ?"
        args = (session_id, limit)
    else:
        sql = f"SELECT {cols} FROM decisions ORDER BY id DESC LIMIT ?"
        args = (limit,)
    async with db.execute(sql, args) as cur:
        rows = []
        async for r in cur:
            d = dict(r)
            d["is_local"] = bool(d["is_local"])
            try:
                d["classification"] = json.loads(d["classification"])
            except (TypeError, ValueError):
                pass
            rows.append(d)
        return rows


def log_decision(
    *,
    session_id: str,
    user_text: str,
    decision: dict,
    log_path: Path | None = None,
) -> None:
    record = {
        "session_id": session_id,
        "user_text_preview": scrub(user_text[:200]),
        "decision": decision,
    }
    line = json.dumps(record)
    log.info("routing_decision %s", line)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
