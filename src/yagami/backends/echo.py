from __future__ import annotations

import asyncio
from typing import AsyncIterator

from .base import Backend, BackendChunk, BackendOptions, Capability, Message


class EchoBackend(Backend):
    name = "echo"
    capabilities = {Capability.TEXT}
    is_local = True

    async def generate(
        self, messages: list[Message], *, options: BackendOptions
    ) -> AsyncIterator[BackendChunk]:
        last_user = next((m for m in reversed(messages) if m.role == "user"), None)
        text = f"echo: {last_user.content if last_user else ''}"
        for word in text.split():
            await asyncio.sleep(0.02)
            yield {"type": "text", "content": word + " ", "meta": {}}
        yield {"type": "done", "content": "", "meta": {}}

    async def health(self) -> bool:
        return True
