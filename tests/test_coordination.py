from __future__ import annotations

import pytest

from yagami.coordination import LocalCoordinator, build_coordinator


@pytest.mark.asyncio
async def test_local_coordinator_enforces_rate_and_recovers_slots():
    coordinator = LocalCoordinator()
    assert await coordinator.rate_limit("project", limit=1, window_seconds=60) is None
    retry_after = await coordinator.rate_limit("project", limit=1, window_seconds=60)
    assert retry_after is not None
    assert retry_after >= 1

    first = await coordinator.acquire_slot("project", limit=1, ttl_seconds=60)
    assert first
    assert await coordinator.acquire_slot("project", limit=1, ttl_seconds=60) is None
    await coordinator.release_slot("project", first)
    second = await coordinator.acquire_slot("project", limit=1, ttl_seconds=60)
    assert second
    assert second != first


def test_coordinator_rejects_non_redis_urls():
    with pytest.raises(ValueError, match="redis"):
        build_coordinator("https://coordination.example")
