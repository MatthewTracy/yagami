from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime, timezone
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
    profile: str | None = None,
) -> int:
    preview = scrub(user_text)[:280]
    classification = decision.get("classification", {})
    source = (
        classification.get("source", "unknown") if isinstance(classification, dict) else "unknown"
    )
    t = timings or {}
    db = get_db()
    cur = await db.execute(
        "INSERT INTO decisions("
        " session_id, created_at, backend, is_local, reason, classification, scrubbed_preview,"
        " source, t_classify_ms, t_first_token_ms, t_total_ms, profile"
        ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            profile or None,
        ),
    )
    await db.commit()
    return cur.lastrowid or 0


async def update_decision_timings(
    decision_id: int,
    *,
    first_token_ms: int | None = None,
    total_ms: int | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost_usd: float | None = None,
) -> None:
    if decision_id <= 0:
        return
    db = get_db()
    await db.execute(
        "UPDATE decisions SET t_first_token_ms = COALESCE(?, t_first_token_ms),"
        " t_total_ms = COALESCE(?, t_total_ms),"
        " tokens_in = COALESCE(?, tokens_in),"
        " tokens_out = COALESCE(?, tokens_out),"
        " cost_usd = COALESCE(?, cost_usd) WHERE id = ?",
        (first_token_ms, total_ms, tokens_in, tokens_out, cost_usd, decision_id),
    )
    await db.commit()


async def list_decisions(*, session_id: str | None = None, limit: int = 100) -> list[dict]:
    db = get_db()
    cols = (
        "id, session_id, created_at, backend, is_local, reason, classification,"
        " scrubbed_preview, source, t_classify_ms, t_first_token_ms, t_total_ms, profile"
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


_EXPORT_HEADER = [
    "id",
    "session_id",
    "created_at_utc",
    "backend",
    "is_local",
    "reason",
    "profile",
    "intent",
    "sensitivity",
    "complexity",
    "source",
    "scrubbed_preview",
    "t_classify_ms",
    "t_first_token_ms",
    "t_total_ms",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "feedback_rating",
]


async def export_decisions_csv(*, session_id: str | None = None, limit: int = 10_000) -> str:
    """Serialize the Privacy Ledger to CSV for compliance/audit export.

    Pulls a few columns `list_decisions()` doesn't surface to the UI table
    (tokens_in/out, cost_usd, feedback rating) since this is meant to be the
    full audit record, not the chat-facing ledger view. User text is never
    included beyond the same scrubbed preview the ledger already stores -
    this export can't leak more than the UI already shows.
    """
    db = get_db()
    cols = (
        "d.id, d.session_id, d.created_at, d.backend, d.is_local, d.reason, d.profile,"
        " d.classification, d.scrubbed_preview, d.t_classify_ms, d.t_first_token_ms,"
        " d.t_total_ms, d.tokens_in, d.tokens_out, d.cost_usd, f.rating AS feedback_rating"
    )
    base = f"SELECT {cols} FROM decisions d LEFT JOIN feedback f ON f.decision_id = d.id"
    if session_id:
        sql = base + " WHERE d.session_id = ? ORDER BY d.id DESC LIMIT ?"
        args: tuple = (session_id, limit)
    else:
        sql = base + " ORDER BY d.id DESC LIMIT ?"
        args = (limit,)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_EXPORT_HEADER)
    async with db.execute(sql, args) as cur:
        async for r in cur:
            row = dict(r)
            try:
                cls = json.loads(row["classification"])
            except (TypeError, ValueError):
                cls = {}
            created_iso = datetime.fromtimestamp(
                row["created_at"] / 1000, tz=timezone.utc
            ).isoformat()
            writer.writerow(
                [
                    row["id"],
                    row["session_id"],
                    created_iso,
                    row["backend"],
                    bool(row["is_local"]),
                    row["reason"],
                    row["profile"] or "",
                    cls.get("intent", ""),
                    cls.get("sensitivity", ""),
                    cls.get("complexity", ""),
                    cls.get("source", ""),
                    row["scrubbed_preview"],
                    row["t_classify_ms"],
                    row["t_first_token_ms"],
                    row["t_total_ms"],
                    row["tokens_in"],
                    row["tokens_out"],
                    row["cost_usd"],
                    row["feedback_rating"],
                ]
            )
    return buf.getvalue()


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
