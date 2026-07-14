from __future__ import annotations

import json
from typing import AsyncIterator

from anthropic import AsyncAnthropic, APIError

from ..config import AnthropicConfig, YagamiConfig
from .base import Backend, BackendChunk, BackendOptions, Capability, Message, Pricing


def build(cfg: YagamiConfig, secrets_get) -> "ClaudeBackend | None":
    key = secrets_get("ANTHROPIC_API_KEY")
    if not key:
        return None
    return ClaudeBackend(cfg.anthropic, key)


class ClaudeBackend(Backend):
    name = "anthropic"
    capabilities = {
        Capability.TEXT,
        Capability.LONG_CONTEXT,
        Capability.CODE,
        Capability.VISION,
        Capability.TOOLS,
    }
    is_local = False
    # Sonnet 4.6 pricing as of 2026-06. Update when switching to Opus 4.8.
    pricing = Pricing(input_per_million_tokens=3.0, output_per_million_tokens=15.0)

    def __init__(self, config: AnthropicConfig, api_key: str) -> None:
        self._config = config
        self._client = AsyncAnthropic(api_key=api_key)

    async def generate(
        self, messages: list[Message], *, options: BackendOptions
    ) -> AsyncIterator[BackendChunk]:
        system_parts = [m.content for m in messages if m.role == "system"]
        chat: list[dict] = []
        for m in messages:
            if m.role == "tool":
                chat.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id,
                                "content": m.content,
                            }
                        ],
                    }
                )
                continue
            if m.role not in ("user", "assistant"):
                continue
            if m.images:
                blocks: list[dict] = []
                for img in m.images:
                    blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": img.media_type,
                                "data": img.data_b64,
                            },
                        }
                    )
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                chat.append({"role": m.role, "content": blocks})
            elif m.role == "assistant" and m.tool_calls:
                blocks: list[dict] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tool_call in m.tool_calls:
                    function = tool_call.get("function", {})
                    arguments = function.get("arguments", "{}")
                    try:
                        parsed_arguments = json.loads(arguments)
                    except (TypeError, ValueError):
                        parsed_arguments = {}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tool_call.get("id"),
                            "name": function.get("name"),
                            "input": parsed_arguments,
                        }
                    )
                chat.append({"role": m.role, "content": blocks})
            else:
                chat.append({"role": m.role, "content": m.content})
        kwargs: dict = {
            "model": self._config.model,
            "max_tokens": options.max_tokens or self._config.max_tokens,
            "temperature": options.temperature,
            "messages": chat,
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)
        try:
            if options.tools:
                kwargs["tools"] = [
                    {
                        "name": tool["function"]["name"],
                        "description": tool["function"].get("description", ""),
                        "input_schema": tool["function"].get(
                            "parameters", {"type": "object", "properties": {}}
                        ),
                    }
                    for tool in options.tools
                ]
                if options.tool_choice is not None:
                    choice = options.tool_choice
                    if choice == "required":
                        kwargs["tool_choice"] = {"type": "any"}
                    elif isinstance(choice, str) and choice in {"auto", "none"}:
                        if choice == "auto":
                            kwargs["tool_choice"] = {"type": "auto"}
                        else:
                            kwargs.pop("tools", None)
                    elif isinstance(choice, dict):
                        name = choice.get("function", {}).get("name")
                        if name:
                            kwargs["tool_choice"] = {"type": "tool", "name": name}
                response = await self._client.messages.create(**kwargs)
                tool_index = 0
                for block in response.content:
                    if block.type == "text":
                        yield {
                            "type": "text",
                            "content": block.text,
                            "meta": {"model": self._config.model},
                        }
                    elif block.type == "tool_use":
                        yield {
                            "type": "tool_call",
                            "content": "",
                            "meta": {
                                "kind": "caller_function",
                                "index": tool_index,
                                "id": block.id,
                                "name": block.name,
                                "arguments": json.dumps(block.input or {}, separators=(",", ":")),
                                "model": self._config.model,
                            },
                        }
                        tool_index += 1
                yield {"type": "done", "content": "", "meta": {"model": self._config.model}}
                return
            async with self._client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield {"type": "text", "content": text, "meta": {"model": self._config.model}}
            yield {"type": "done", "content": "", "meta": {"model": self._config.model}}
        except APIError as exc:
            yield {"type": "error", "content": f"anthropic error: {exc}", "meta": {}}
            yield {"type": "done", "content": "", "meta": {"model": self._config.model}}

    async def health(self) -> bool:
        return True

    async def close(self) -> None:
        await self._client.close()
