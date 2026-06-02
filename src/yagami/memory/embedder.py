"""Embedding client backed by Ollama's `/api/embeddings` endpoint.

Reuses the already-hot Ollama daemon — no torch dep, no second model server.
Default model is `all-minilm` (384 dim, ~45MB), picked because it fits in
constrained disk and is one of Ollama's smallest decent embedding models.

Swap the model via [memory] embedding_model in yagami.toml. Vector schema
(observations_vec) is float[384] — changing the dim requires a migration.
"""

from __future__ import annotations

import logging

import httpx

EMBED_DIM = 384  # all-minilm dimension; must match the vec0 schema

log = logging.getLogger("yagami.memory.embed")


class Embedder:
    def __init__(self, url: str = "http://localhost:11434", model: str = "all-minilm") -> None:
        self._url = url
        self._model = model
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
                json={"model": self._model, "prompt": text},
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
