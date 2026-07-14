"""Generic policy-only upstream for an existing OpenAI-compatible gateway."""

from __future__ import annotations

from ..config import YagamiConfig
from .base import Capability, Pricing
from .openai_compat import OpenAICompatBackend


class UpstreamBackend(OpenAICompatBackend):
    name = "upstream"
    capabilities = {
        Capability.TEXT,
        Capability.CODE,
        Capability.LONG_CONTEXT,
        Capability.TOOLS,
        Capability.VISION,
    }
    # The upstream gateway owns provider-specific accounting.
    pricing = Pricing()


def build(cfg: YagamiConfig, secrets_get) -> UpstreamBackend | None:
    upstream = cfg.upstream
    if not upstream.enabled:
        return None
    key = secrets_get(upstream.api_key_env)
    if not key and not upstream.allow_unauthenticated:
        return None
    return UpstreamBackend(
        api_key=key or "yagami-no-auth",
        base_url=upstream.base_url,
        model=upstream.model or "yagami-auto",
        max_tokens=upstream.max_tokens,
        capabilities=set(UpstreamBackend.capabilities),
    )
