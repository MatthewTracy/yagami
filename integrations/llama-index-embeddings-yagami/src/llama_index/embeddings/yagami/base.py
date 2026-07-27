"""Yagami embeddings implemented on LlamaIndex's OpenAI-compatible contract."""

from __future__ import annotations

import os
from typing import Any

from llama_index.embeddings.openai_like import OpenAILikeEmbedding


class YagamiEmbedding(OpenAILikeEmbedding):
    """Embedding model whose inputs are governed by Yagami policy."""

    def __init__(
        self,
        *,
        model_name: str = "yagami-embedding",
        base_url: str | None = None,
        api_key: str | None = None,
        dimensions: int = 384,
        **kwargs: Any,
    ) -> None:
        api_base = (base_url or os.getenv("YAGAMI_BASE_URL") or "http://127.0.0.1:8000/v1").rstrip(
            "/"
        )
        token = api_key if api_key is not None else os.getenv("YAGAMI_API_KEY", "")
        headers = dict(kwargs.pop("default_headers", {}) or {})
        headers.setdefault("user-agent", "llama-index-embeddings-yagami")
        super().__init__(
            model_name=model_name,
            api_base=api_base,
            api_key=token or "yagami-local",
            dimensions=dimensions,
            default_headers=headers,
            **kwargs,
        )

    @classmethod
    def class_name(cls) -> str:
        return "YagamiEmbedding"
