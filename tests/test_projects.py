from __future__ import annotations

import json

import pytest

from yagami.chat.session import SessionStore
from yagami.projects import ProjectGovernor, ProjectLimitError, ProjectRegistry
from yagami.storage.db import now_ms


def _registry(tmp_path, document: dict) -> ProjectRegistry:
    path = tmp_path / "projects.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return ProjectRegistry(path)


@pytest.mark.asyncio
async def test_project_purpose_jurisdiction_and_rate_limits(tmp_path) -> None:
    registry = _registry(
        tmp_path,
        {
            "defaults": {
                "requests_per_minute": 1,
                "max_concurrent_requests": 1,
                "allowed_purposes": ["support"],
                "allowed_jurisdictions": ["US"],
            }
        },
    )
    governor = ProjectGovernor(registry)
    await governor.check_request(project_id="p", purpose="support", jurisdiction="US")
    with pytest.raises(ProjectLimitError) as rate:
        await governor.check_request(project_id="p", purpose="support", jurisdiction="US")
    assert rate.value.code == "rate_limit_exceeded"

    with pytest.raises(ProjectLimitError) as purpose:
        await governor.check_request(project_id="other", purpose="sales", jurisdiction="US")
    assert purpose.value.code == "purpose_not_allowed"
    with pytest.raises(ProjectLimitError) as jurisdiction:
        await governor.check_request(project_id="third", purpose="support", jurisdiction="EU")
    assert jurisdiction.value.code == "jurisdiction_not_allowed"


@pytest.mark.asyncio
async def test_project_concurrency_limit(tmp_path) -> None:
    governor = ProjectGovernor(
        _registry(
            tmp_path,
            {"defaults": {"requests_per_minute": 10, "max_concurrent_requests": 1}},
        )
    )
    async with governor.slot("p"):
        with pytest.raises(ProjectLimitError) as error:
            async with governor.slot("p"):
                pass
        assert error.value.code == "concurrency_limit_exceeded"


@pytest.mark.asyncio
async def test_project_daily_spend_cap(fresh_db, tmp_path) -> None:
    governor = ProjectGovernor(
        _registry(
            tmp_path,
            {
                "defaults": {
                    "requests_per_minute": 10,
                    "max_concurrent_requests": 1,
                    "daily_spend_usd": 1.0,
                }
            },
        )
    )
    session_id = await SessionStore().new_session()
    await fresh_db.execute(
        "INSERT INTO decisions(session_id, created_at, backend, is_local, reason,"
        " classification, scrubbed_preview, source, cost_usd, project_id)"
        " VALUES(?, ?, 'cloud', 0, 'test', '{}', '', 'test', 1.25, 'p')",
        (session_id, now_ms()),
    )
    await fresh_db.commit()
    assert await governor.spend_blocked("p") is True
    assert await governor.spend_blocked("other") is False
