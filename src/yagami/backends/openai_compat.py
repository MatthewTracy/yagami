"""Shared implementation for backends that speak the OpenAI Chat Completions
wire format through the official `openai` SDK pointed at a different
`base_url`. Groq, OpenRouter, and Gemini all publish OpenAI-compatible
endpoints; Mistral's API is close enough to work the same way. This isn't a
backend itself - it has no `build()`, so the registry skips it (see
backends/registry.py: only modules exposing `build` are treated as
backends).

`openai.py`'s OpenAIBackend predates this and stays a separate, self-
contained implementation - it's the one most likely to be read as the
"canonical" example (see README's "Adding your own backend"), so it's worth
keeping simple and not routed through an inherited base a new contributor
has to go find.
"""

from __future__ import annotations

from typing import AsyncIterator

from openai import APIError, AsyncOpenAI

from .base import Backend, BackendChunk, BackendOptions, Capability, Message, Pricing


class OpenAICompatBackend(Backend):
    """Generic OpenAI-wire-format backend. Subclasses set `name` and
    `pricing`, and pass model/base_url/api_key into `__init__`."""

    is_local = False

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        max_tokens: int,
        capabilities: set[Capability] | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        if capabilities is not None:
            self.capabilities = capabilities

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
                model=self._model,
                messages=chat,  # type: ignore[arg-type]
                max_tokens=options.max_tokens or self._max_tokens,
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
                    yield {"type": "text", "content": delta.content, "meta": {"model": self._model}}
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
                            "model": self._model,
                        },
                    }
            yield {"type": "done", "content": "", "meta": {"model": self._model}}
        except APIError as exc:
            yield {"type": "error", "content": f"{self.name} error: {exc}", "meta": {}}
            yield {"type": "done", "content": "", "meta": {"model": self._model}}

    async def health(self) -> bool:
        return True

    async def close(self) -> None:
        await self._client.close()


__all__ = ["OpenAICompatBackend", "Capability", "Pricing"]
