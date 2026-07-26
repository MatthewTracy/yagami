"""Deadline-aware retries and per-backend circuit breaking."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
import weakref
from dataclasses import dataclass
from typing import AsyncIterator

from .base import Backend, BackendChunk, BackendOptions, Message
from .errors import ProviderFailure, ProviderFailureKind, from_exception

log = logging.getLogger("yagami.retry")

_TRANSIENT_HINTS = (
    "timeout",
    "timed out",
    "connection",
    "503",
    "502",
    "504",
    "529",
    "rate limit",
    "overloaded",
    "temporarily",
)
_MAX_ATTEMPTS = 3
_BASE_DELAY_S = 0.6
_CIRCUIT_THRESHOLD = 3
_CIRCUIT_COOLDOWN_S = 30.0


@dataclass
class _Circuit:
    failures: int = 0
    open_until: float = 0.0


_circuits: weakref.WeakKeyDictionary[object, _Circuit] = weakref.WeakKeyDictionary()


def _is_transient(chunk: BackendChunk) -> bool:
    meta = chunk.get("meta", {})
    if "retryable" in meta:
        return bool(meta["retryable"])
    low = chunk["content"].lower()
    return any(hint in low for hint in _TRANSIENT_HINTS)


def _retry_after(chunk: BackendChunk) -> float | None:
    try:
        value = chunk.get("meta", {}).get("retry_after")
        return max(0.0, min(float(value), 3600.0)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _record_failure(circuit: _Circuit) -> None:
    circuit.failures += 1
    if circuit.failures >= _CIRCUIT_THRESHOLD:
        circuit.open_until = time.monotonic() + _CIRCUIT_COOLDOWN_S


def _record_success(circuit: _Circuit) -> None:
    circuit.failures = 0
    circuit.open_until = 0.0


async def generate_with_retry(
    backend: Backend, messages: list[Message], options: BackendOptions
) -> AsyncIterator[BackendChunk]:
    circuit = _circuits.setdefault(backend, _Circuit())
    now = time.monotonic()
    if circuit.open_until > now:
        retry_after = round(circuit.open_until - now, 3)
        yield {
            "type": "error",
            "content": "provider circuit is open",
            "meta": {
                "code": "provider_circuit_open",
                "provider": backend.name,
                "retryable": True,
                "status_code": 503,
                "retry_after": retry_after,
            },
        }
        yield {
            "type": "done",
            "content": "",
            "meta": {"retry_aborted": True, "retry_after": retry_after},
        }
        return
    if circuit.open_until:
        try:
            healthy = await backend.health()
        except Exception:  # noqa: BLE001 - health is advisory
            healthy = False
        if not healthy:
            circuit.open_until = time.monotonic() + _CIRCUIT_COOLDOWN_S
            yield {
                "type": "error",
                "content": "provider remains unhealthy",
                "meta": {
                    "code": "provider_unhealthy",
                    "provider": backend.name,
                    "retryable": True,
                    "status_code": 503,
                    "retry_after": _CIRCUIT_COOLDOWN_S,
                },
            }
            yield {"type": "done", "content": "", "meta": {"retry_aborted": True}}
            return
        _record_success(circuit)

    deadline = time.monotonic() + options.deadline_seconds
    for attempt in range(_MAX_ATTEMPTS):
        produced_real_content = False
        error_chunk: BackendChunk | None = None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            async with asyncio.timeout(remaining):
                async for chunk in backend.generate(messages, options=options):
                    if chunk["type"] in ("text", "image_url"):
                        produced_real_content = True
                        _record_success(circuit)
                        yield chunk
                    elif chunk["type"] == "error":
                        if produced_real_content:
                            yield chunk
                        else:
                            error_chunk = chunk
                    elif chunk["type"] == "done":
                        if error_chunk and not produced_real_content:
                            break
                        _record_success(circuit)
                        yield chunk
                        return
                    else:
                        yield chunk
        except TimeoutError:
            failure = ProviderFailure(
                provider=backend.name,
                kind=ProviderFailureKind.TIMEOUT,
                retryable=True,
                status_code=504,
            )
            error_chunk = failure.chunk()
        except ProviderFailure as exc:
            error_chunk = exc.chunk()
        except Exception as exc:  # noqa: BLE001 - normalize provider SDK failures
            log.warning(
                "backend %s raised on attempt %s: %s",
                backend.name,
                attempt + 1,
                type(exc).__name__,
            )
            error_chunk = from_exception(backend.name, exc).chunk()

        if error_chunk is None:
            _record_success(circuit)
            return
        _record_failure(circuit)
        if not _is_transient(error_chunk) or attempt + 1 >= _MAX_ATTEMPTS:
            meta = {
                **error_chunk.get("meta", {}),
                "attempts": attempt + 1,
                "retries_exhausted": attempt + 1 >= _MAX_ATTEMPTS,
            }
            message = error_chunk["content"]
            if attempt + 1 >= _MAX_ATTEMPTS:
                message = f"{message} (retries exhausted)"
            yield {"type": "error", "content": message, "meta": meta}
            yield {"type": "done", "content": "", "meta": {"retry_aborted": True}}
            return
        delay = max(_BASE_DELAY_S * (2**attempt), _retry_after(error_chunk) or 0.0)
        jitter = secrets.randbelow(1001) / 1000 * min(0.25, delay * 0.25)
        delay += jitter
        if time.monotonic() + delay >= deadline:
            failure = ProviderFailure(
                provider=backend.name,
                kind=ProviderFailureKind.TIMEOUT,
                retryable=True,
                status_code=504,
            )
            yield failure.chunk()
            yield {"type": "done", "content": "", "meta": {"retry_aborted": True}}
            return
        log.info(
            "retrying %s in %.2fs (attempt %s)",
            backend.name,
            delay,
            attempt + 2,
        )
        await asyncio.sleep(delay)
