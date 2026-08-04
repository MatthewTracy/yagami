from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any, AsyncIterator

import httpx

from ..config import OllamaConfig, YagamiConfig
from .base import Backend, BackendChunk, BackendOptions, Capability, Message, Pricing, TrustZone
from .errors import from_exception


log = logging.getLogger("yagami.backends.ollama")


def build(cfg: YagamiConfig, _secrets_get) -> "OllamaBackend":
    return OllamaBackend(cfg.ollama)


class OllamaBackend(Backend):
    name = "ollama"
    capabilities = {Capability.TEXT, Capability.CODE}
    is_local = True
    trust_zone = TrustZone.DEVICE
    pricing = Pricing()  # local - free

    def __init__(self, config: OllamaConfig) -> None:
        self._config = config
        self.trust_zone = config.trust_zone
        self.is_local = config.trust_zone.is_private
        self._client = httpx.AsyncClient(base_url=config.url, timeout=httpx.Timeout(120.0))
        self._warmup_status = "pending" if config.preload_models else "disabled"
        self._warmup_text_models: tuple[str, ...] = ()
        self._warmup_embedding_models: tuple[str, ...] = ()

    async def generate(
        self, messages: list[Message], *, options: BackendOptions
    ) -> AsyncIterator[BackendChunk]:
        model = options.model_override or options.lora_variant or self._config.model
        wire_msgs = _build_wire_messages(messages, options.system_prompt)
        body = {
            "model": model,
            "messages": wire_msgs,
            "stream": True,
            "keep_alive": self._config.keep_alive,
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
            yield from_exception(self.name, exc).chunk()
            yield {"type": "done", "content": "", "meta": {"model": model}}

    async def health(self) -> bool:
        try:
            r = await self._client.get("/api/tags")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def configure_warmup(
        self,
        *,
        text_models: Iterable[str],
        embedding_models: Iterable[str] = (),
    ) -> None:
        """Record the models used by this process without loading them.

        The list is also exposed through the authenticated health endpoint so
        operators can see which configured models are resident. Model names
        are configuration, never prompt or customer content.
        """
        self._warmup_text_models = _dedupe_models(text_models)
        self._warmup_embedding_models = _dedupe_models(embedding_models)

    async def preload_configured_models(self) -> None:
        """Load configured models in the background for performance mode.

        An unavailable or oversized model degrades warmup only; it must never
        prevent Yagami from starting. The ordinary request path will still
        return the provider's typed error if that model is later selected.
        """
        if not self._config.preload_models:
            self._warmup_status = "disabled"
            return
        self._warmup_status = "warming"
        failures = 0
        for model in self._warmup_text_models:
            try:
                response = await self._client.post(
                    "/api/chat",
                    json={
                        "model": model,
                        "messages": [],
                        "stream": False,
                        "keep_alive": self._config.keep_alive,
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                failures += 1
                log.warning("could not preload Ollama text model %s: %s", model, exc)
        for model in self._warmup_embedding_models:
            try:
                response = await self._client.post(
                    "/api/embeddings",
                    json={
                        "model": model,
                        "prompt": "warmup",
                        "keep_alive": self._config.keep_alive,
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                failures += 1
                log.warning("could not preload Ollama embedding model %s: %s", model, exc)
        self._warmup_status = "degraded" if failures else "ready"

    async def loaded_models(self) -> set[str]:
        try:
            response = await self._client.get("/api/ps", timeout=2.0)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return set()
        models = payload.get("models", []) if isinstance(payload, dict) else []
        loaded: set[str] = set()
        if isinstance(models, list):
            for item in models:
                if not isinstance(item, dict):
                    continue
                for key in ("name", "model"):
                    value = item.get(key)
                    if isinstance(value, str) and value:
                        loaded.add(_model_key(value))
        return loaded

    async def is_model_loaded(self, model: str | None = None) -> bool:
        return _model_key(model or self._config.model) in await self.loaded_models()

    async def runtime_status(self) -> dict[str, Any]:
        loaded = await self.loaded_models()
        configured = _dedupe_models((*self._warmup_text_models, *self._warmup_embedding_models))
        return {
            "profile": self._config.performance_profile,
            "keep_alive": self._config.keep_alive,
            "preload_enabled": self._config.preload_models,
            "warmup_status": self._warmup_status,
            "models": [
                {"name": model, "loaded": _model_key(model) in loaded} for model in configured
            ],
        }

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


def _model_key(model: str) -> str:
    normalized = model.strip().casefold()
    return normalized[:-7] if normalized.endswith(":latest") else normalized


def _dedupe_models(models: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for model in models:
        value = model.strip()
        key = _model_key(value)
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return tuple(out)
