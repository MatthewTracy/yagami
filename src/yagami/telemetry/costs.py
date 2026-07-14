"""Cost accounting for backend calls.

Each Backend declares its own Pricing on the class (v0.2.13). This module
is the SQL aggregation layer + the token-count heuristic; pricing math
lives on the backend so future plugins price themselves.
"""

from __future__ import annotations

from datetime import datetime

from ..backends.base import Backend, Pricing
from ..storage.db import get_db


def estimate_cost(
    backend: Backend | None,
    *,
    tokens_in: int = 0,
    tokens_out: int = 0,
    images: int = 0,
) -> float:
    """Estimate USD cost of a call using `backend.pricing`.

    Accepts the backend instance (preferred) or None for "unknown" (returns 0).
    The legacy string-name path is supported via the secondary function below
    for callers that don't have the backend object handy.
    """
    if backend is None:
        return 0.0
    p: Pricing = getattr(backend, "pricing", Pricing())
    return (
        (tokens_in / 1_000_000) * p.input_per_million_tokens
        + (tokens_out / 1_000_000) * p.output_per_million_tokens
        + images * p.per_image_usd
    )


def estimate_cost_by_name(
    backend_name: str,
    backends: dict[str, Backend],
    *,
    tokens_in: int = 0,
    tokens_out: int = 0,
    images: int = 0,
) -> float:
    """Look up a backend by name from the registered dict; useful when
    stream.py only has the name in hand."""
    b = backends.get(backend_name)
    return estimate_cost(b, tokens_in=tokens_in, tokens_out=tokens_out, images=images)


def rough_token_count(text: str) -> int:
    """4 chars/token rule of thumb. Good enough for cost estimation."""
    if not text:
        return 0
    return max(1, len(text) // 4)


async def spend_today_usd() -> float:
    """Sum cost_usd since midnight in the machine's local timezone."""
    cutoff_ms = _local_day_start_ms()
    db = get_db()
    async with db.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM decisions WHERE created_at >= ?",
        (cutoff_ms,),
    ) as cur:
        row = await cur.fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


async def spend_project_today_usd(project_id: str) -> float:
    cutoff_ms = _local_day_start_ms()
    db = get_db()
    async with db.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM decisions WHERE project_id=? AND created_at>=?",
        (project_id, cutoff_ms),
    ) as cursor:
        row = await cursor.fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


def _local_day_start_ms(now: datetime | None = None) -> int:
    if now is None:
        local_now = datetime.now().astimezone()
    elif now.tzinfo is None:
        local_now = now.astimezone()
    else:
        local_now = now
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp() * 1000)


async def spend_session_usd(session_id: str) -> float:
    db = get_db()
    async with db.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM decisions WHERE session_id=?",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0
