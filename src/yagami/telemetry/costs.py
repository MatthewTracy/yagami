"""Cost accounting for backend calls.

Pricing table is per-million-token for text models, per-image for image gen.
Local backends are free (Ollama). Numbers are approximate and worth keeping
in sync with each provider's posted prices.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..storage.db import get_db


@dataclass(frozen=True)
class Pricing:
    input_per_mtok: float  # USD per 1M input tokens
    output_per_mtok: float  # USD per 1M output tokens
    per_image: float  # USD per image generation


# Conservative defaults. Local models = $0.
_PRICING: dict[str, Pricing] = {
    "echo": Pricing(0.0, 0.0, 0.0),
    "ollama": Pricing(0.0, 0.0, 0.0),
    # Claude Sonnet 4.6 list price as of May 2026.
    "anthropic": Pricing(input_per_mtok=3.0, output_per_mtok=15.0, per_image=0.0),
    # Stability AI stable-image-core list price.
    "stability": Pricing(input_per_mtok=0.0, output_per_mtok=0.0, per_image=0.03),
}


def estimate_cost(
    backend_name: str,
    *,
    tokens_in: int = 0,
    tokens_out: int = 0,
    images: int = 0,
) -> float:
    p = _PRICING.get(backend_name)
    if p is None:
        return 0.0
    return (
        (tokens_in / 1_000_000) * p.input_per_mtok
        + (tokens_out / 1_000_000) * p.output_per_mtok
        + images * p.per_image
    )


def rough_token_count(text: str) -> int:
    """4 chars/token rule of thumb. Good enough for cost estimation."""
    if not text:
        return 0
    return max(1, len(text) // 4)


async def spend_today_usd() -> float:
    """Sum cost_usd for decisions created in the last 24h."""
    db = get_db()
    async with db.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM decisions"
        " WHERE created_at >= (strftime('%s', 'now', '-1 day') * 1000)"
    ) as cur:
        row = await cur.fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


async def spend_session_usd(session_id: str) -> float:
    db = get_db()
    async with db.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM decisions WHERE session_id=?",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0
