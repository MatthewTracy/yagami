"""Groq backend, via Groq's OpenAI-compatible chat completions endpoint.

Groq's pitch is speed (LPU inference) over frontier-model quality - useful
as a very fast, cheap cloud fallback distinct from the local Ollama path.
Requires GROQ_API_KEY in the OS keyring (or .env) - see secrets.py.
"""

from __future__ import annotations

from ..config import YagamiConfig
from .base import Capability, Pricing
from .openai_compat import OpenAICompatBackend

_BASE_URL = "https://api.groq.com/openai/v1"

# llama-3.3-70b-versatile pricing as of 2026-06. Swapping models changes this.
_PRICING = Pricing(input_per_million_tokens=0.59, output_per_million_tokens=0.79)


def build(cfg: YagamiConfig, secrets_get) -> "GroqBackend | None":
    key = secrets_get("GROQ_API_KEY")
    if not key:
        return None
    return GroqBackend(cfg, key)


class GroqBackend(OpenAICompatBackend):
    name = "groq"
    capabilities = {Capability.TEXT, Capability.CODE}
    pricing = _PRICING

    def __init__(self, cfg: YagamiConfig, api_key: str) -> None:
        super().__init__(
            api_key=api_key,
            base_url=_BASE_URL,
            model=cfg.groq.model,
            max_tokens=cfg.groq.max_tokens,
        )
