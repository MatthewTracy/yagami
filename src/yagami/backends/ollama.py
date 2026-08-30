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
    capabilities = {Capability.TEXT, Capability.CODE, Capability.TOOLS}
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
        if options.tools:
            body["tools"] = options.tools
        if options.tool_choice is not None and options.tool_choice != "auto":
            yield {
                "type": "error",
                "content": "Ollama's native API does not support forced tool_choice",
                "meta": {"model": model, "code": "tool_choice_not_supported"},
            }
            yield {"type": "done", "content": "", "meta": {"model": model}}
            return
        try:
            async with self._client.stream("POST", "/api/chat", json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    message = data.get("message")
                    if isinstance(message, dict):
                        if content := message.get("content"):
                            yield {
                                "type": "text",
                                "content": str(content),
                                "meta": {"model": model},
                            }
                        for index, call in enumerate(message.get("tool_calls") or []):
                            chunk = _tool_call_chunk(call, fallback_index=index, model=model)
                            if chunk is not None:
                                yield chunk
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

    async def has_model(self, model: str | None = None) -> bool:
        """Return whether the configured model is installed in Ollama.

        Demo startup uses this bounded probe to choose a real local model only
        when the next request can actually run. An unreachable service or an
        invalid response is a normal unavailable result, never a boot failure.
        """
        try:
            response = await self._client.get("/api/tags", timeout=2.0)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return False
        models = payload.get("models", []) if isinstance(payload, dict) else []
        wanted = _model_key(model or self._config.model)
        if not isinstance(models, list):
            return False
        for item in models:
            if not isinstance(item, dict):
                continue
            for key in ("name", "model"):
                value = item.get(key)
                if isinstance(value, str) and _model_key(value) == wanted:
                    return True
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
    out: list[dict] = []
    if system_prompt is not None:
        out.append({"role": "system", "content": system_prompt})
    call_names: dict[str, str] = {}
    for m in messages:
        if m.role == "system" and system_prompt is not None:
            continue
        wire: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.role == "assistant" and m.tool_calls:
            native_calls: list[dict[str, Any]] = []
            for index, raw in enumerate(m.tool_calls):
                function = raw.get("function") if isinstance(raw, dict) else None
                if not isinstance(function, dict):
                    continue
                name = str(function.get("name") or "")
                if not name:
                    continue
                arguments = function.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}
                native_calls.append(
                    {
                        "type": "function",
                        "function": {
                            "index": index,
                            "name": name,
                            "arguments": arguments,
                        },
                    }
                )
                call_id = raw.get("id")
                if isinstance(call_id, str) and call_id:
                    call_names[call_id] = name
            if native_calls:
                wire["tool_calls"] = native_calls
        elif m.role == "tool":
            tool_name = call_names.get(m.tool_call_id or "") or m.name
            if tool_name:
                wire["tool_name"] = tool_name
        out.append(wire)
    return out


def _tool_call_chunk(raw: object, *, fallback_index: int, model: str) -> BackendChunk | None:
    if not isinstance(raw, dict):
        return None
    function = raw.get("function")
    if not isinstance(function, dict):
        return None
    name = function.get("name")
    if not isinstance(name, str) or not name:
        return None
    arguments = function.get("arguments", {})
    if isinstance(arguments, dict):
        encoded_arguments = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    elif isinstance(arguments, str):
        encoded_arguments = arguments
    else:
        encoded_arguments = "{}"
    raw_index = function.get("index", raw.get("index", fallback_index))
    if not isinstance(raw_index, (int, str)):
        index = fallback_index
    else:
        try:
            index = int(raw_index)
        except ValueError:
            index = fallback_index
    return {
        "type": "tool_call",
        "content": "",
        "meta": {
            "kind": "caller_function",
            "index": index,
            "id": raw.get("id"),
            "name": name,
            "arguments": encoded_arguments,
            "model": model,
        },
    }


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
