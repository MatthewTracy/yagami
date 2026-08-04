"""Embedding client backed by Ollama's `/api/embeddings` endpoint.

Reuses the already-hot Ollama daemon - no torch dep, no second model server.
Default model is `all-minilm` (384 dim, ~45MB), picked because it fits in
constrained disk and is one of Ollama's smallest decent embedding models.

Swap the model via [memory] embedding_model in yagami.toml. Vector schema
(observations_vec) is float[384] - changing the dim requires a migration.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import httpx
from openai import APIError, AsyncOpenAI

from ..config import YagamiConfig

EMBED_DIM = 384  # all-minilm dimension; must match the vec0 schema

log = logging.getLogger("yagami.memory.embed")


@runtime_checkable
class EmbedderProtocol(Protocol):
    @property
    def model(self) -> str: ...

    async def embed(self, text: str) -> list[float] | None: ...

    async def close(self) -> None: ...


class Embedder:
    def __init__(
        self,
        url: str = "http://localhost:11434",
        model: str = "all-minilm",
        *,
        keep_alive: str = "5m",
    ) -> None:
        self._url = url
        self._model = model
        self._keep_alive = keep_alive
        self._client = httpx.AsyncClient(base_url=url, timeout=httpx.Timeout(60.0))

    @property
    def model(self) -> str:
        return self._model

    async def embed(self, text: str) -> list[float] | None:
        """Return the embedding vector, or None on any error (caller should
        mark the observation 'failed' so the worker doesn't keep retrying)."""
        if not text:
            return None
        try:
            r = await self._client.post(
                "/api/embeddings",
                json={
                    "model": self._model,
                    "prompt": text,
                    "keep_alive": self._keep_alive,
                },
            )
            r.raise_for_status()
            vec = r.json().get("embedding")
            if not isinstance(vec, list) or len(vec) != EMBED_DIM:
                log.warning(
                    "embedding returned unexpected shape: type=%s len=%s",
                    type(vec).__name__,
                    len(vec) if isinstance(vec, list) else "?",
                )
                return None
            return [float(x) for x in vec]
        except httpx.HTTPError as exc:
            log.warning("embed call failed: %s", exc)
            return None

    async def close(self) -> None:
        await self._client.aclose()


class OpenAICompatibleEmbedder:
    """384-dimension embeddings from an OpenAI-compatible endpoint."""

    def __init__(self, *, url: str, model: str, api_key: str = "") -> None:
        self._model = model
        self._client = AsyncOpenAI(
            base_url=url,
            api_key=api_key or "yagami-local-embeddings",
            timeout=60.0,
        )

    @property
    def model(self) -> str:
        return self._model

    async def embed(self, text: str) -> list[float] | None:
        if not text:
            return None
        try:
            response = await self._client.embeddings.create(
                model=self._model,
                input=text,
                dimensions=EMBED_DIM,
            )
            vector = response.data[0].embedding
            if len(vector) != EMBED_DIM:
                log.warning("embedding returned %d dimensions; expected %d", len(vector), EMBED_DIM)
                return None
            return [float(value) for value in vector]
        except (APIError, IndexError, TypeError, ValueError) as exc:
            log.warning("OpenAI-compatible embed call failed: %s", exc)
            return None

    async def close(self) -> None:
        await self._client.close()


def build_embedder(cfg: YagamiConfig, secrets_get) -> EmbedderProtocol | None:
    memory = cfg.memory
    if memory.embedding_provider == "none":
        return None
    if memory.embedding_provider == "ollama":
        return Embedder(
            url=memory.embedding_url or cfg.ollama.url,
            model=memory.embedding_model,
            keep_alive=cfg.ollama.keep_alive,
        )
    key = secrets_get(memory.embedding_api_key_env) if memory.embedding_api_key_env else ""
    return OpenAICompatibleEmbedder(
        url=memory.embedding_url,
        model=memory.embedding_model,
        api_key=key or "",
    )
