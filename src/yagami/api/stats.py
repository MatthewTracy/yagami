"""Aggregate stats over the decisions table.

Pure SQL — no LLM calls, no per-turn cost. Lets the user (and future
dashboard) see "where did my money go?" / "which backend do I actually use?"
without reading raw rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query

from ..storage.db import get_db

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("")
async def stats(days: int = Query(default=14, ge=1, le=365)) -> dict:
    db = get_db()
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

    async with db.execute(
        """
        SELECT backend,
               COUNT(*)               AS turns,
               SUM(COALESCE(cost_usd, 0))           AS cost_usd,
               SUM(COALESCE(tokens_in, 0))          AS tokens_in,
               SUM(COALESCE(tokens_out, 0))         AS tokens_out,
               AVG(NULLIF(t_first_token_ms, 0))     AS avg_ttft_ms,
               AVG(NULLIF(t_total_ms, 0))           AS avg_total_ms
        FROM decisions
        WHERE created_at >= ?
        GROUP BY backend
        ORDER BY turns DESC
        """,
        (cutoff_ms,),
    ) as cur:
        by_backend = [
            {
                "backend": row[0],
                "turns": row[1],
                "cost_usd": round(row[2] or 0.0, 4),
                "tokens_in": row[3] or 0,
                "tokens_out": row[4] or 0,
                "avg_ttft_ms": int(row[5]) if row[5] else None,
                "avg_total_ms": int(row[6]) if row[6] else None,
            }
            async for row in cur
        ]

    async with db.execute(
        """
        SELECT DATE(created_at / 1000, 'unixepoch') AS day,
               SUM(COALESCE(cost_usd, 0))          AS cost_usd,
               COUNT(*)                            AS turns
        FROM decisions
        WHERE created_at >= ?
        GROUP BY day
        ORDER BY day ASC
        """,
        (cutoff_ms,),
    ) as cur:
        by_day = [
            {"day": row[0], "cost_usd": round(row[1] or 0.0, 4), "turns": row[2]}
            async for row in cur
        ]

    async with db.execute(
        """
        SELECT json_extract(classification, '$.source') AS source,
               COUNT(*) AS turns
        FROM decisions
        WHERE created_at >= ?
        GROUP BY source
        ORDER BY turns DESC
        """,
        (cutoff_ms,),
    ) as cur:
        by_source = [{"source": row[0] or "(unknown)", "turns": row[1]} async for row in cur]

    async with db.execute(
        "SELECT COUNT(*), SUM(COALESCE(cost_usd, 0)) FROM decisions WHERE created_at >= ?",
        (cutoff_ms,),
    ) as cur:
        row = await cur.fetchone()
        total_turns = row[0] if row else 0
        total_cost = round((row[1] or 0.0), 4) if row else 0.0

    return {
        "window_days": days,
        "total_turns": total_turns,
        "total_cost_usd": total_cost,
        "by_backend": by_backend,
        "by_day": by_day,
        "by_classification_source": by_source,
    }
