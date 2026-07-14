from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator, Literal, Protocol, TypedDict, runtime_checkable

from pydantic import BaseModel, Field, field_validator


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
    media_type: Literal["image/png", "image/jpeg", "image/gif", "image/webp"]
    data_b64: str = Field(min_length=1, max_length=27_962_028)

    @field_validator("data_b64")
    @classmethod
    def validate_base64(cls, value: str) -> str:
        """Reject malformed or over-20MB image payloads at the WS boundary."""
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("image data must be valid base64") from exc
        if len(decoded) > 20 * 1024 * 1024:
            raise ValueError("decoded image exceeds 20 MB")
        return value


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    images: list[ImageAttachment] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class BackendOptions(BaseModel):
    temperature: float = 0.7
    max_tokens: int = 2048
    lora_variant: str | None = None
    model_override: str | None = None
    system_prompt: str | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None


class BackendChunk(TypedDict):
    type: Literal["text", "image_url", "tool_call", "error", "done"]
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
