"""OpenAI Chat Completions backend.

Uses the official `openai` Python SDK pointed at any OpenAI-compatible base
URL. Works against api.openai.com out of the box; can also target OpenRouter,
Groq, Together, Fireworks, etc. by overriding `[openai] base_url` in
yagami.toml.

Requires OPENAI_API_KEY in the OS keyring (or .env) - see secrets.py.
"""

from __future__ import annotations

from typing import AsyncIterator

from openai import APIError, AsyncOpenAI

from ..config import OpenAIConfig, YagamiConfig
from .base import Backend, BackendChunk, BackendOptions, Capability, Message, Pricing


def build(cfg: YagamiConfig, secrets_get) -> "OpenAIBackend | None":
    key = secrets_get("OPENAI_API_KEY")
    if not key:
        return None
    return OpenAIBackend(cfg.openai, key)


# Conservative gpt-4.1-mini pricing as of 2026-06. Override per-deployment
# via the model name → callers that swap to gpt-4o or gpt-4.1 will undercount
# until pricing is parameterized; good enough for the first cut.
_DEFAULT_PRICING = Pricing(
    input_per_million_tokens=0.40,
    output_per_million_tokens=1.60,
)


class OpenAIBackend(Backend):
    name = "openai"
    capabilities = {
        Capability.TEXT,
        Capability.LONG_CONTEXT,
        Capability.CODE,
        Capability.TOOLS,
        Capability.VISION,
    }
    is_local = False
    pricing = _DEFAULT_PRICING

    def __init__(self, config: OpenAIConfig, api_key: str) -> None:
        self._config = config
        self._client = AsyncOpenAI(api_key=api_key, base_url=config.base_url)

    async def generate(
        self, messages: list[Message], *, options: BackendOptions
    ) -> AsyncIterator[BackendChunk]:
        system_parts = [m.content for m in messages if m.role == "system"]
        if options.system_prompt:
            system_parts = [options.system_prompt]

        chat: list[dict] = []
        if system_parts:
            chat.append({"role": "system", "content": "\n\n".join(system_parts)})
        for m in messages:
            if m.role == "tool":
                chat.append(
                    {
                        "role": "tool",
                        "content": m.content,
                        "tool_call_id": m.tool_call_id,
                    }
                )
                continue
            if m.role not in ("user", "assistant"):
                continue
            if m.images:
                content: list[dict] = []
                for img in m.images:
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{img.media_type};base64,{img.data_b64}"},
                        }
                    )
                if m.content:
                    content.append({"type": "text", "text": m.content})
                chat.append({"role": m.role, "content": content})
            else:
                item: dict = {"role": m.role, "content": m.content or None}
                if m.role == "assistant" and m.tool_calls:
                    item["tool_calls"] = m.tool_calls
                chat.append(item)

        try:
            kwargs: dict = dict(
                model=self._config.model,
                messages=chat,
                max_tokens=options.max_tokens or self._config.max_tokens,
                temperature=options.temperature,
                stream=True,
            )
            if options.tools:
                kwargs["tools"] = options.tools
                if options.tool_choice is not None:
                    kwargs["tool_choice"] = options.tool_choice
            stream = await self._client.chat.completions.create(**kwargs)
            async for event in stream:
                if not event.choices:
                    continue
                delta = event.choices[0].delta
                if delta and delta.content:
                    yield {
                        "type": "text",
                        "content": delta.content,
                        "meta": {"model": self._config.model},
                    }
                for tool_call in delta.tool_calls or [] if delta else []:
                    function = tool_call.function
                    yield {
                        "type": "tool_call",
                        "content": "",
                        "meta": {
                            "kind": "caller_function",
                            "index": tool_call.index,
                            "id": tool_call.id,
                            "name": function.name if function else None,
                            "arguments": function.arguments if function else None,
                            "model": self._config.model,
                        },
                    }
            yield {"type": "done", "content": "", "meta": {"model": self._config.model}}
        except APIError as exc:
            yield {"type": "error", "content": f"openai error: {exc}", "meta": {}}
            yield {"type": "done", "content": "", "meta": {"model": self._config.model}}

    async def health(self) -> bool:
        return True

    async def close(self) -> None:
        await self._client.close()
