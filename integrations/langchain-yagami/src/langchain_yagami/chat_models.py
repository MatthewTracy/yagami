"""LangChain chat model backed by Yagami's governed OpenAI-compatible API."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

import httpx
from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field, PrivateAttr, SecretStr

_EVIDENCE_HEADERS = {
    "request_id": "x-yagami-request-id",
    "decision_id": "x-yagami-decision-id",
    "backend": "x-yagami-backend",
    "policy_hash": "x-yagami-policy-hash",
}


def _message_payload(message: BaseMessage) -> dict[str, Any]:
    role = {
        "human": "user",
        "ai": "assistant",
        "system": "system",
        "tool": "tool",
    }.get(message.type, message.type)
    payload: dict[str, Any] = {"role": role, "content": message.content}
    if isinstance(message, ToolMessage):
        payload["tool_call_id"] = message.tool_call_id
    if isinstance(message, AIMessage) and message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.get("id") or f"call_{index}",
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": json.dumps(call.get("args", {}), separators=(",", ":")),
                },
            }
            for index, call in enumerate(message.tool_calls)
        ]
    if message.name:
        payload["name"] = message.name
    return payload


def _tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        result.append(
            {
                "name": str(function.get("name") or ""),
                "args": arguments,
                "id": str(call.get("id") or ""),
                "type": "tool_call",
            }
        )
    return result


def _evidence(response: httpx.Response) -> dict[str, str]:
    return {
        key: response.headers[header]
        for key, header in _EVIDENCE_HEADERS.items()
        if header in response.headers
    }


class ChatYagami(BaseChatModel):
    """A LangChain model whose context and tool use are enforced by Yagami."""

    model: str = "yagami-auto"
    base_url: str = Field(
        default_factory=lambda: os.getenv("YAGAMI_BASE_URL", "http://127.0.0.1:8000/v1")
    )
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(os.getenv("YAGAMI_API_KEY", "")))
    timeout: float = 120.0
    temperature: float = 0.7
    max_tokens: int = 2048
    metadata: dict[str, Any] = Field(default_factory=dict)
    _client: httpx.Client | None = PrivateAttr(default=None)
    _async_client: httpx.AsyncClient | None = PrivateAttr(default=None)

    @property
    def _llm_type(self) -> str:
        return "yagami"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model": self.model, "base_url": self.base_url}

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json", "user-agent": "langchain-yagami"}
        secret = self.api_key.get_secret_value()
        if secret:
            headers["authorization"] = f"Bearer {secret}"
        return headers

    def _request_payload(
        self,
        messages: list[BaseMessage],
        *,
        stream: bool,
        stop: list[str] | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": kwargs.pop("model", self.model),
            "messages": [_message_payload(message) for message in messages],
            "stream": stream,
            "temperature": kwargs.pop("temperature", self.temperature),
            "max_tokens": kwargs.pop("max_tokens", self.max_tokens),
            "metadata": {**self.metadata, **kwargs.pop("metadata", {})},
        }
        if stop:
            payload["stop"] = stop
        payload.update(kwargs)
        return payload

    @staticmethod
    def _result(response: httpx.Response) -> ChatResult:
        response.raise_for_status()
        body = response.json()
        choice = body["choices"][0]
        message = choice["message"]
        usage = body.get("usage") or {}
        ai_message = AIMessage(
            content=message.get("content") or "",
            tool_calls=_tool_calls(message),
            response_metadata={
                "finish_reason": choice.get("finish_reason"),
                "model": body.get("model"),
                "yagami": _evidence(response),
            },
            usage_metadata={
                "input_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
            },
        )
        return ChatResult(generations=[ChatGeneration(message=ai_message)])

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del run_manager
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, headers=self._headers)
        response = self._client.post(
            f"{self.base_url.rstrip('/')}/chat/completions",
            json=self._request_payload(messages, stream=False, stop=stop, **kwargs),
        )
        return self._result(response)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del run_manager
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=self.timeout, headers=self._headers)
        response = await self._async_client.post(
            f"{self.base_url.rstrip('/')}/chat/completions",
            json=self._request_payload(messages, stream=False, stop=stop, **kwargs),
        )
        return self._result(response)

    @staticmethod
    def _stream_chunk(data: str) -> ChatGenerationChunk | None:
        if not data or data == "[DONE]":
            return None
        body = json.loads(data)
        choice = body["choices"][0]
        delta = choice.get("delta") or {}
        chunk = AIMessageChunk(
            content=delta.get("content") or "",
            response_metadata={
                "finish_reason": choice.get("finish_reason"),
                "model": body.get("model"),
            },
        )
        return ChatGenerationChunk(message=chunk)

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, headers=self._headers)
        with self._client.stream(
            "POST",
            f"{self.base_url.rstrip('/')}/chat/completions",
            json=self._request_payload(messages, stream=True, stop=stop, **kwargs),
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                chunk = self._stream_chunk(line[6:])
                if chunk is not None:
                    if run_manager and chunk.text:
                        run_manager.on_llm_new_token(chunk.text, chunk=chunk)
                    yield chunk

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(timeout=self.timeout, headers=self._headers)
        async with self._async_client.stream(
            "POST",
            f"{self.base_url.rstrip('/')}/chat/completions",
            json=self._request_payload(messages, stream=True, stop=stop, **kwargs),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                chunk = self._stream_chunk(line[6:])
                if chunk is not None:
                    if run_manager and chunk.text:
                        await run_manager.on_llm_new_token(chunk.text, chunk=chunk)
                    yield chunk

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | BaseTool | Any],
        *,
        tool_choice: str | bool | dict[str, Any] | None = None,
        **kwargs: Any,
    ):
        formatted = [convert_to_openai_tool(tool) for tool in tools]
        return super().bind(tools=formatted, tool_choice=tool_choice, **kwargs)
