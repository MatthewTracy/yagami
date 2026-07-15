from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import httpx

from ..config import FoundryLocalConfig, YagamiConfig
from .openai_compat import Capability, OpenAICompatBackend, Pricing


def build(cfg: YagamiConfig, _secrets_get) -> "FoundryLocalBackend | None":
    if not cfg.foundry_local.enabled:
        return None
    return FoundryLocalBackend(cfg.foundry_local)


def _service_urls(base_url: str) -> tuple[str, str]:
    """Return the OpenAI API URL and Foundry service root.

    Foundry's port is dynamic, and its service endpoints live at the root
    while Chat Completions lives under /v1.
    """

    parsed = urlsplit(base_url.rstrip("/"))
    root = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return f"{root}/v1", root


class FoundryLocalBackend(OpenAICompatBackend):
    name = "foundry_local"
    is_local = True
    capabilities = {
        Capability.TEXT,
        Capability.CODE,
        Capability.LONG_CONTEXT,
        Capability.TOOLS,
    }
    pricing = Pricing()

    def __init__(self, config: FoundryLocalConfig) -> None:
        api_url, service_root = _service_urls(config.base_url)
        super().__init__(
            api_key="foundry-local",
            base_url=api_url,
            model=config.model,
            max_tokens=config.max_tokens,
            capabilities=set(self.capabilities),
        )
        self._health_client = httpx.AsyncClient(
            base_url=service_root,
            timeout=httpx.Timeout(5.0),
        )

    async def health(self) -> bool:
        try:
            response = await self._health_client.get("/openai/status")
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                return False
            endpoints = data.get("Endpoints", data.get("endpoints"))
            return isinstance(endpoints, list) and bool(endpoints)
        except (httpx.HTTPError, ValueError, TypeError):
            return False

    async def close(self) -> None:
        await self._health_client.aclose()
        await super().close()
