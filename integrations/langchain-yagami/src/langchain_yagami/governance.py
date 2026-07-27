"""Explicit policy preflight and approval operations for LangChain applications."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

import httpx
from langchain_core.messages import BaseMessage

from .chat_models import _message_payload


class YagamiGovernanceClient:
    """Thin client for governance operations that do not execute a model."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("YAGAMI_BASE_URL") or "http://127.0.0.1:8000/v1"
        ).rstrip("/")
        token = api_key if api_key is not None else os.getenv("YAGAMI_API_KEY", "")
        headers = {"user-agent": "langchain-yagami"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        self._client = httpx.Client(timeout=timeout, headers=headers)
        self._async_client = httpx.AsyncClient(timeout=timeout, headers=headers)

    def preview(
        self,
        messages: Sequence[BaseMessage],
        *,
        model: str = "yagami-auto",
        metadata: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        response = self._client.post(
            f"{self.base_url}/policy/preview",
            json={
                "model": model,
                "messages": [_message_payload(message) for message in messages],
                "metadata": metadata or {},
                "tools": tools,
            },
        )
        response.raise_for_status()
        return response.json()

    async def apreview(
        self,
        messages: Sequence[BaseMessage],
        *,
        model: str = "yagami-auto",
        metadata: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        response = await self._async_client.post(
            f"{self.base_url}/policy/preview",
            json={
                "model": model,
                "messages": [_message_payload(message) for message in messages],
                "metadata": metadata or {},
                "tools": tools,
            },
        )
        response.raise_for_status()
        return response.json()

    def approve_tools(
        self,
        tools: list[str],
        *,
        subject_id: str,
        schema_hash: str,
        purpose: str,
        ttl_seconds: int = 900,
        ticket: str | None = None,
    ) -> dict[str, Any]:
        response = self._client.post(
            f"{self.base_url}/tool-approvals",
            json={
                "tools": tools,
                "subject_id": subject_id,
                "schema_hash": schema_hash,
                "purpose": purpose,
                "ttl_seconds": ttl_seconds,
                "ticket": ticket,
            },
        )
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._client.close()

    async def aclose(self) -> None:
        await self._async_client.aclose()
