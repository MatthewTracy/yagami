from __future__ import annotations

from fastapi import APIRouter, Query

from ..config import effective_routing, get_config
from ..telemetry.costs import spend_session_usd, spend_today_usd

router = APIRouter(prefix="/api/costs", tags=["costs"])


@router.get("")
async def costs(session_id: str | None = Query(default=None)) -> dict:
    today = await spend_today_usd()
    session = await spend_session_usd(session_id) if session_id else 0.0
    cap = effective_routing(get_config()).daily_spend_cap_usd
    return {
        "today_usd": round(today, 4),
        "session_usd": round(session, 4),
        "daily_cap_usd": cap,
        "cap_remaining_usd": max(0.0, cap - today) if cap > 0 else None,
        "cap_exceeded": cap > 0 and today >= cap,
    }
