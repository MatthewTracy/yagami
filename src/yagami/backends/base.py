from __future__ import annotations

from enum import Enum
from typing import AsyncIterator, Literal, Protocol, TypedDict, runtime_checkable

from pydantic import BaseModel


class Capability(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    LONG_CONTEXT = "long_context"
    CODE = "code"
    VISION = "vision"


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

    async def generate(
        self, messages: list[Message], *, options: BackendOptions
    ) -> AsyncIterator[BackendChunk]: ...

    async def health(self) -> bool: ...
