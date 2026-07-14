from __future__ import annotations

import base64
from typing import AsyncIterator

import httpx

from ..config import StabilityConfig, YagamiConfig
from .base import Backend, BackendChunk, BackendOptions, Capability, Message, Pricing


def build(cfg: YagamiConfig, secrets_get) -> "StabilityImageBackend | None":
    key = secrets_get("STABILITY_API_KEY")
    if not key:
        return None
    return StabilityImageBackend(cfg.stability, key)


class StabilityImageBackend(Backend):
    name = "stability"
    capabilities = {Capability.IMAGE}
    is_local = False
    # Stable Image Core: $0.03/image as of 2026-06.
    pricing = Pricing(per_image_usd=0.03)

    def __init__(self, config: StabilityConfig, api_key: str) -> None:
        self._config = config
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            base_url="https://api.stability.ai", timeout=httpx.Timeout(60.0)
        )

    async def generate(
        self, messages: list[Message], *, options: BackendOptions
    ) -> AsyncIterator[BackendChunk]:
        prompt = next((m.content for m in reversed(messages) if m.role == "user"), "")
        if not prompt:
            yield {"type": "error", "content": "empty prompt", "meta": {}}
            return
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "image/*"}
        data = {"prompt": prompt, "output_format": "png"}
        try:
            r = await self._client.post(
                f"/v2beta/stable-image/generate/{self._config.model.split('-')[-1]}",
                headers=headers,
                files={"none": (None, "")},
                data=data,
            )
            r.raise_for_status()
            b64 = base64.b64encode(r.content).decode()
            data_url = f"data:image/png;base64,{b64}"
            yield {
                "type": "image_url",
                "content": data_url,
                "meta": {"model": self._config.model, "prompt": prompt},
            }
            yield {"type": "done", "content": "", "meta": {}}
        except httpx.HTTPError as exc:
            yield {"type": "error", "content": f"stability error: {exc}", "meta": {}}
            yield {"type": "done", "content": "", "meta": {"model": self._config.model}}

    async def health(self) -> bool:
        return True

    async def close(self) -> None:
        await self._client.aclose()
