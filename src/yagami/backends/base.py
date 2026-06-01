from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Literal, Protocol, TypedDict, runtime_checkable

from pydantic import BaseModel


class Capability(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    LONG_CONTEXT = "long_context"
    CODE = "code"
    VISION = "vision"
    TOOLS = "tools"  # backend supports tool/function calling (v0.3 surface)


@dataclass(frozen=True)
class Pricing:
    """Per-backend cost model. Local backends use the default zeros.

    The 4 chars/token estimate in telemetry/costs.rough_token_count is good
    enough for routing decisions and cost displays. Real token-accurate
    accounting would need each backend to report token counts back, which
    Ollama/Stability don't.
    """

    input_per_million_tokens: float = 0.0
    output_per_million_tokens: float = 0.0
    per_image_usd: float = 0.0


class ImageAttachment(BaseModel):
    media_type: str  # "image/png" / "image/jpeg" / etc.
    data_b64: str  # raw base64, no data: prefix


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str
    images: list[ImageAttachment] | None = None


class BackendOptions(BaseModel):
    temperature: float = 0.7
    max_tokens: int = 2048
    lora_variant: str | None = None
    system_prompt: str | None = None


class BackendChunk(TypedDict):
    type: Literal["text", "image_url", "error", "done"]
    content: str
    meta: dict


@runtime_checkable
class Backend(Protocol):
    name: str
    capabilities: set[Capability]
    is_local: bool
    pricing: Pricing

    async def generate(
        self, messages: list[Message], *, options: BackendOptions
    ) -> AsyncIterator[BackendChunk]: ...

    async def health(self) -> bool: ...
