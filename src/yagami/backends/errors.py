"""Typed, content-free provider failures."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .base import BackendChunk


class ProviderFailureKind(str, Enum):
    AUTHENTICATION = "authentication"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INVALID_REQUEST = "invalid_request"
    CANCELLED = "cancelled"
    INTERNAL = "internal"


@dataclass(frozen=True)
class ProviderFailure(Exception):
    provider: str
    kind: ProviderFailureKind
    retryable: bool
    status_code: int
    retry_after: float | None = None

    @property
    def code(self) -> str:
        return f"provider_{self.kind.value}"

    @property
    def safe_message(self) -> str:
        return {
            ProviderFailureKind.AUTHENTICATION: "provider authentication failed",
            ProviderFailureKind.RATE_LIMITED: "provider rate limit exceeded",
            ProviderFailureKind.TIMEOUT: "provider request timed out",
            ProviderFailureKind.UNAVAILABLE: "provider is temporarily unavailable",
            ProviderFailureKind.INVALID_REQUEST: "provider rejected the request",
            ProviderFailureKind.CANCELLED: "provider request was cancelled",
            ProviderFailureKind.INTERNAL: "provider request failed",
        }[self.kind]

    def chunk(self) -> BackendChunk:
        meta: dict[str, Any] = {
            "code": self.code,
            "provider": self.provider,
            "retryable": self.retryable,
            "status_code": self.status_code,
        }
        if self.retry_after is not None:
            meta["retry_after"] = self.retry_after
        return {"type": "error", "content": self.safe_message, "meta": meta}


def from_exception(provider: str, exc: BaseException) -> ProviderFailure:
    response = getattr(exc, "response", None)
    status = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
    try:
        status_code = int(status) if status is not None else 502
    except (TypeError, ValueError):
        status_code = 502
    retry_after: float | None = None
    headers = getattr(response, "headers", None)
    if headers is not None:
        value = headers.get("Retry-After")
        try:
            retry_after = max(0.0, min(float(value), 3600.0)) if value is not None else None
        except (TypeError, ValueError):
            retry_after = None
    name = type(exc).__name__.casefold()
    if status_code in {401, 403} or "authentication" in name or "permission" in name:
        kind = ProviderFailureKind.AUTHENTICATION
    elif status_code == 429 or "ratelimit" in name:
        kind = ProviderFailureKind.RATE_LIMITED
    elif status_code in {408, 504} or "timeout" in name:
        kind = ProviderFailureKind.TIMEOUT
    elif status_code >= 500 or "connection" in name:
        kind = ProviderFailureKind.UNAVAILABLE
    elif 400 <= status_code < 500:
        kind = ProviderFailureKind.INVALID_REQUEST
    else:
        kind = ProviderFailureKind.INTERNAL
    return ProviderFailure(
        provider=provider,
        kind=kind,
        retryable=kind
        in {
            ProviderFailureKind.RATE_LIMITED,
            ProviderFailureKind.TIMEOUT,
            ProviderFailureKind.UNAVAILABLE,
        },
        status_code=status_code,
        retry_after=retry_after,
    )
