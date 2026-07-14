"""OpenRouter backend, via OpenRouter's OpenAI-compatible chat completions
endpoint. OpenRouter fronts dozens of providers/models behind one API key -
this single backend effectively unlocks all of them by changing
`[openrouter] model` in config, no new code required.

Requires OPENROUTER_API_KEY in the OS keyring (or .env) - see secrets.py.
"""

from __future__ import annotations

from ..config import YagamiConfig
from .base import Capability, Pricing
from .openai_compat import OpenAICompatBackend

_BASE_URL = "https://openrouter.ai/api/v1"

# Pricing is genuinely per-model on OpenRouter (it's a router over dozens of
# providers), so a fixed Pricing constant here is only ever approximate. Left
# at 0 rather than guessing - the cost meter will undercount this backend
# until pricing is looked up per-model. See CostMeter.tsx / telemetry/costs.py.
_PRICING = Pricing()


def build(cfg: YagamiConfig, secrets_get) -> "OpenRouterBackend | None":
    key = secrets_get("OPENROUTER_API_KEY")
    if not key:
        return None
    return OpenRouterBackend(cfg, key)


class OpenRouterBackend(OpenAICompatBackend):
    name = "openrouter"
    capabilities = {
        Capability.TEXT,
        Capability.LONG_CONTEXT,
        Capability.CODE,
        Capability.VISION,
        Capability.TOOLS,
    }
    pricing = _PRICING

    def __init__(self, cfg: YagamiConfig, api_key: str) -> None:
        super().__init__(
            api_key=api_key,
            base_url=_BASE_URL,
            model=cfg.openrouter.model,
            max_tokens=cfg.openrouter.max_tokens,
        )
