"""Yagami LLM implemented on LlamaIndex's OpenAI-compatible contract."""

from __future__ import annotations

import os
from typing import Any

from llama_index.llms.openai_like import OpenAILike


class YagamiLLM(OpenAILike):
    """LlamaIndex LLM that sends every model and tool call through Yagami."""

    def __init__(
        self,
        *,
        model: str = "yagami-auto",
        base_url: str | None = None,
        api_key: str | None = None,
        context_window: int = 131_072,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        api_base = (base_url or os.getenv("YAGAMI_BASE_URL") or "http://127.0.0.1:8000/v1").rstrip(
            "/"
        )
        token = api_key if api_key is not None else os.getenv("YAGAMI_API_KEY", "")
        headers = dict(kwargs.pop("default_headers", {}) or {})
        headers.setdefault("user-agent", "llama-index-llms-yagami")
        additional = dict(kwargs.pop("additional_kwargs", {}) or {})
        if metadata:
            additional["metadata"] = metadata
        super().__init__(
            model=model,
            api_base=api_base,
            api_key=token or "yagami-local",
            context_window=context_window,
            is_chat_model=True,
            is_function_calling_model=True,
            temperature=temperature,
            max_tokens=max_tokens,
            default_headers=headers,
            additional_kwargs=additional,
            **kwargs,
        )

    @classmethod
    def class_name(cls) -> str:
        return "YagamiLLM"
