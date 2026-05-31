"""Exponential-backoff retry wrapper for Backend.generate.

Yields the same chunk stream as the underlying backend. If the first chunk(s)
indicate a known-transient failure (5xx, timeout, rate-limited) before any
content has been delivered, we silently retry up to N times. Once a `text` or
`image_url` chunk has been emitted, retries are off — we can't resume.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from .base import Backend, BackendChunk, BackendOptions, Message

log = logging.getLogger("yagami.retry")

_TRANSIENT_HINTS = (
    "timeout",
    "timed out",
    "connection",
    "503",
    "502",
    "504",
    "529",  # Anthropic overloaded
    "rate limit",
    "overloaded",
    "temporarily",
)

_MAX_ATTEMPTS = 3
_BASE_DELAY_S = 0.6


def _is_transient(message: str) -> bool:
    low = message.lower()
    return any(h in low for h in _TRANSIENT_HINTS)


async def generate_with_retry(
    backend: Backend, messages: list[Message], options: BackendOptions
) -> AsyncIterator[BackendChunk]:
    for attempt in range(_MAX_ATTEMPTS):
        produced_real_content = False
        error_chunk: BackendChunk | None = None
        try:
            async for chunk in backend.generate(messages, options=options):
                if chunk["type"] in ("text", "image_url"):
                    produced_real_content = True
                    yield chunk
                elif chunk["type"] == "error":
                    if produced_real_content:
                        yield chunk
                    else:
                        error_chunk = chunk
                elif chunk["type"] == "done":
                    if error_chunk and not produced_real_content:
                        # Defer the done — we may retry.
                        break
                    yield chunk
                    return
                else:
                    yield chunk
        except Exception as exc:  # pragma: no cover - depends on backend
            log.warning("backend %s raised on attempt %s: %s", backend.name, attempt + 1, exc)
            error_chunk = {"type": "error", "content": str(exc), "meta": {}}

        # No early error → stream completed; we're done.
        if error_chunk is None:
            return
        # An error happened pre-content; decide retry.
        if not _is_transient(error_chunk["content"]) or attempt + 1 >= _MAX_ATTEMPTS:
            msg = error_chunk["content"]
            if attempt + 1 >= _MAX_ATTEMPTS:
                msg = f"{msg} (retries exhausted)"
            yield {"type": "error", "content": msg, "meta": error_chunk.get("meta", {})}
            yield {"type": "done", "content": "", "meta": {"retry_aborted": True}}
            return
        delay = _BASE_DELAY_S * (2**attempt)
        log.info("retrying %s in %.1fs (attempt %s)", backend.name, delay, attempt + 2)
        await asyncio.sleep(delay)
