"""Distributed project limits and locks for multi-replica deployments."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Protocol


class Coordinator(Protocol):
    async def rate_limit(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> int | None:
        """Return retry-after seconds when denied, otherwise ``None``."""

    async def acquire_slot(
        self,
        key: str,
        *,
        limit: int,
        ttl_seconds: int,
    ) -> str | None: ...

    async def release_slot(self, key: str, token: str) -> None: ...

    async def close(self) -> None: ...


class LocalCoordinator:
    """Single-process coordinator used by workstation and SQLite deployments."""

    def __init__(self) -> None:
        self._rate_lock = asyncio.Lock()
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._slot_lock = asyncio.Lock()
        self._slots: dict[str, dict[str, float]] = defaultdict(dict)

    async def rate_limit(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> int | None:
        now = time.monotonic()
        async with self._rate_lock:
            requests = self._requests[key]
            while requests and requests[0] <= now - window_seconds:
                requests.popleft()
            if len(requests) >= limit:
                return max(1, int(window_seconds - (now - requests[0])))
            requests.append(now)
        return None

    async def acquire_slot(
        self,
        key: str,
        *,
        limit: int,
        ttl_seconds: int,
    ) -> str | None:
        now = time.monotonic()
        async with self._slot_lock:
            slots = self._slots[key]
            expired = [token for token, expires_at in slots.items() if expires_at <= now]
            for token in expired:
                slots.pop(token, None)
            if len(slots) >= limit:
                return None
            token = uuid.uuid4().hex
            slots[token] = now + ttl_seconds
            return token

    async def release_slot(self, key: str, token: str) -> None:
        async with self._slot_lock:
            self._slots[key].pop(token, None)

    async def close(self) -> None:
        return None


_RATE_SCRIPT = """
local now_parts = redis.call('TIME')
local now_ms = (now_parts[1] * 1000) + math.floor(now_parts[2] / 1000)
local cutoff = now_ms - tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', cutoff)
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[1]) then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  local retry_ms = tonumber(ARGV[2])
  if oldest[2] then
    retry_ms = math.max(1, tonumber(oldest[2]) + tonumber(ARGV[2]) - now_ms)
  end
  return math.ceil(retry_ms / 1000)
end
redis.call('ZADD', KEYS[1], now_ms, ARGV[3])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]) + 1000)
return 0
"""

_SLOT_SCRIPT = """
local now_parts = redis.call('TIME')
local now_ms = (now_parts[1] * 1000) + math.floor(now_parts[2] / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then
  return 0
end
redis.call('ZADD', KEYS[1], now_ms + tonumber(ARGV[2]), ARGV[3])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]) + 1000)
return 1
"""


class RedisCoordinator:
    """Redis-backed atomic coordination with TTL recovery after replica loss."""

    def __init__(self, client: Any, *, prefix: str = "yagami") -> None:
        self._client = client
        self._prefix = prefix.strip(":") or "yagami"

    def _key(self, kind: str, key: str) -> str:
        return f"{self._prefix}:{kind}:{key}"

    async def rate_limit(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> int | None:
        retry_after = int(
            await self._client.eval(
                _RATE_SCRIPT,
                1,
                self._key("rate", key),
                limit,
                window_seconds * 1000,
                uuid.uuid4().hex,
            )
        )
        return retry_after or None

    async def acquire_slot(
        self,
        key: str,
        *,
        limit: int,
        ttl_seconds: int,
    ) -> str | None:
        token = uuid.uuid4().hex
        acquired = int(
            await self._client.eval(
                _SLOT_SCRIPT,
                1,
                self._key("slots", key),
                limit,
                ttl_seconds * 1000,
                token,
            )
        )
        return token if acquired else None

    async def release_slot(self, key: str, token: str) -> None:
        await self._client.zrem(self._key("slots", key), token)

    async def close(self) -> None:
        await self._client.aclose()


def build_coordinator(url: str, *, prefix: str = "yagami") -> Coordinator:
    if not url:
        return LocalCoordinator()
    if not url.casefold().startswith(("redis://", "rediss://")):
        raise ValueError("YAGAMI_COORDINATION_URL must use redis:// or rediss://")
    try:
        from redis.asyncio import Redis
    except ImportError as exc:
        raise RuntimeError(
            "Redis coordination requires the production extra: pip install 'yagami[production]'"
        ) from exc
    return RedisCoordinator(
        Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            health_check_interval=30,
        ),
        prefix=prefix,
    )
