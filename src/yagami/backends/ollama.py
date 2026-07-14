from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from ..config import OllamaConfig, YagamiConfig
from .base import Backend, BackendChunk, BackendOptions, Capability, Message, Pricing


def build(cfg: YagamiConfig, _secrets_get) -> "OllamaBackend":
    return OllamaBackend(cfg.ollama)


class OllamaBackend(Backend):
    name = "ollama"
    capabilities = {Capability.TEXT, Capability.CODE}
    is_local = True
    pricing = Pricing()  # local - free

    def __init__(self, config: OllamaConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(base_url=config.url, timeout=httpx.Timeout(120.0))

    async def generate(
        self, messages: list[Message], *, options: BackendOptions
    ) -> AsyncIterator[BackendChunk]:
        model = options.model_override or options.lora_variant or self._config.model
        wire_msgs = _build_wire_messages(messages, options.system_prompt)
        body = {
            "model": model,
            "messages": wire_msgs,
            "stream": True,
            "options": {"temperature": options.temperature, "num_predict": options.max_tokens},
        }
        try:
            async with self._client.stream("POST", "/api/chat", json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if "message" in data and (content := data["message"].get("content")):
                        yield {"type": "text", "content": content, "meta": {"model": model}}
                    if data.get("done"):
                        yield {"type": "done", "content": "", "meta": {"model": model}}
                        return
        except httpx.HTTPError as exc:
            yield {"type": "error", "content": f"ollama error: {exc}", "meta": {}}
            yield {"type": "done", "content": "", "meta": {"model": model}}

    async def health(self) -> bool:
        try:
            r = await self._client.get("/api/tags")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        await self._client.aclose()


def _build_wire_messages(messages: list[Message], system_prompt: str | None) -> list[dict]:
    if system_prompt is None:
        return [{"role": m.role, "content": m.content} for m in messages]
    out: list[dict] = [{"role": "system", "content": system_prompt}]
    for m in messages:
        if m.role == "system":
            continue
        out.append({"role": m.role, "content": m.content})
    return out
