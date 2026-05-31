from __future__ import annotations

from typing import AsyncIterator

from anthropic import AsyncAnthropic, APIError

from ..config import AnthropicConfig
from .base import Backend, BackendChunk, BackendOptions, Capability, Message


class ClaudeBackend(Backend):
    name = "anthropic"
    capabilities = {Capability.TEXT, Capability.LONG_CONTEXT, Capability.CODE, Capability.VISION}
    is_local = False

    def __init__(self, config: AnthropicConfig, api_key: str) -> None:
        self._config = config
        self._client = AsyncAnthropic(api_key=api_key)

    async def generate(
        self, messages: list[Message], *, options: BackendOptions
    ) -> AsyncIterator[BackendChunk]:
        system_parts = [m.content for m in messages if m.role == "system"]
        chat: list[dict] = []
        for m in messages:
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
            async with self._client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield {"type": "text", "content": text, "meta": {"model": self._config.model}}
            yield {"type": "done", "content": "", "meta": {"model": self._config.model}}
        except APIError as exc:
            yield {"type": "error", "content": f"anthropic error: {exc}", "meta": {}}
            yield {"type": "done", "content": "", "meta": {"model": self._config.model}}

    async def health(self) -> bool:
        return True
