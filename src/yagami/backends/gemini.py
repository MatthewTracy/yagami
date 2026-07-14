"""Google Gemini backend, via Gemini's OpenAI-compatible endpoint
(generativelanguage.googleapis.com/v1beta/openai/) rather than the native
google-genai SDK - keeps this backend dependency-free, same pattern as
groq.py / openrouter.py / mistral.py.

Requires GEMINI_API_KEY in the OS keyring (or .env) - see secrets.py.
"""

from __future__ import annotations

from ..config import YagamiConfig
from .base import Capability, Pricing
from .openai_compat import OpenAICompatBackend

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# gemini-2.5-flash pricing as of 2026-06. Swapping to gemini-2.5-pro changes this.
_PRICING = Pricing(input_per_million_tokens=0.30, output_per_million_tokens=2.50)


def build(cfg: YagamiConfig, secrets_get) -> "GeminiBackend | None":
    key = secrets_get("GEMINI_API_KEY")
    if not key:
        return None
    return GeminiBackend(cfg, key)


class GeminiBackend(OpenAICompatBackend):
    name = "gemini"
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
            model=cfg.gemini.model,
            max_tokens=cfg.gemini.max_tokens,
        )
