from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from yagami.coordination import build_coordinator

_REDIS_URL = os.getenv("YAGAMI_TEST_REDIS_URL", "")
pytestmark = pytest.mark.skipif(
    not _REDIS_URL,
    reason="set YAGAMI_TEST_REDIS_URL to run Redis coordination tests",
)


@pytest.mark.asyncio
async def test_redis_coordinates_independent_replicas() -> None:
    prefix = f"yagami-test-shared-{uuid4().hex}"
    first = build_coordinator(_REDIS_URL, prefix=prefix)
    second = build_coordinator(_REDIS_URL, prefix=prefix)
    try:
        assert await first.rate_limit("project", limit=1, window_seconds=60) is None
        assert await second.rate_limit("project", limit=1, window_seconds=60) is not None

        token = await first.acquire_slot("project", limit=1, ttl_seconds=60)
        assert token
        assert await second.acquire_slot("project", limit=1, ttl_seconds=60) is None
        await second.release_slot("project", token)
        assert await second.acquire_slot("project", limit=1, ttl_seconds=60)
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_redis_slot_recovers_after_lease_expiry() -> None:
    coordinator = build_coordinator(_REDIS_URL, prefix=f"yagami-test-expiry-{uuid4().hex}")
    try:
        assert await coordinator.acquire_slot("project", limit=1, ttl_seconds=1)
        assert await coordinator.acquire_slot("project", limit=1, ttl_seconds=1) is None
        await asyncio.sleep(1.1)
        assert await coordinator.acquire_slot("project", limit=1, ttl_seconds=1)
    finally:
        await coordinator.close()
