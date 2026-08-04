from __future__ import annotations

import asyncio
from typing import AsyncIterator

from ..config import YagamiConfig
from .base import Backend, BackendChunk, BackendOptions, Capability, Message, Pricing, TrustZone


def build(_cfg: YagamiConfig, _secrets_get) -> "EchoBackend":
    return EchoBackend()


class EchoBackend(Backend):
    name = "echo"
    capabilities = {Capability.TEXT}
    is_local = True
    trust_zone = TrustZone.DEVICE
    pricing = Pricing()

    async def generate(
        self, messages: list[Message], *, options: BackendOptions
    ) -> AsyncIterator[BackendChunk]:
        text = (
            "Policy-only demo: Yagami processed this request locally, but no configured "
            "Ollama model is available, so this is not an AI-generated answer. Run "
            "`ollama pull llama3.2:3b-instruct-q4_K_M` and restart Yagami for local "
            "model-backed responses."
        )
        for word in text.split():
            await asyncio.sleep(0.02)
            yield {"type": "text", "content": word + " ", "meta": {}}
        yield {"type": "done", "content": "", "meta": {}}

    async def health(self) -> bool:
        return True
