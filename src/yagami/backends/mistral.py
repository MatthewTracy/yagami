"""Mistral backend, via the OpenAI-compatible chat completions endpoint
Mistral publishes at api.mistral.ai.

Requires MISTRAL_API_KEY in the OS keyring (or .env) - see secrets.py. This
is the real version of the Mistral backend the README's "Adding your own
backend" section shows as a worked example.
"""

from __future__ import annotations

from ..config import YagamiConfig
from .base import Capability, Pricing
from .openai_compat import OpenAICompatBackend

_BASE_URL = "https://api.mistral.ai/v1"

# mistral-large-latest pricing as of 2026-06. Override per-deployment via the
# model name; swapping to a different Mistral model will undercount until
# pricing is parameterized per model - same caveat as backends/openai.py.
_PRICING = Pricing(input_per_million_tokens=2.0, output_per_million_tokens=6.0)


def build(cfg: YagamiConfig, secrets_get) -> "MistralBackend | None":
    key = secrets_get("MISTRAL_API_KEY")
    if not key:
        return None
    return MistralBackend(cfg, key)


class MistralBackend(OpenAICompatBackend):
    name = "mistral"
    capabilities = {Capability.TEXT, Capability.LONG_CONTEXT, Capability.CODE, Capability.TOOLS}
    pricing = _PRICING

    def __init__(self, cfg: YagamiConfig, api_key: str) -> None:
        super().__init__(
            api_key=api_key,
            base_url=_BASE_URL,
            model=cfg.mistral.model,
            max_tokens=cfg.mistral.max_tokens,
        )
