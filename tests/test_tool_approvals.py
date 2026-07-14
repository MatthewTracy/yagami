from __future__ import annotations

import pytest

from yagami.governance import ApprovalError, ApprovalStore


@pytest.mark.asyncio
async def test_approval_capability_is_one_time_and_purpose_bound(fresh_db) -> None:
    store = ApprovalStore()
    grant = await store.create(
        project_id="alpha",
        tools=["payment.*"],
        purpose="billing",
        ticket="CHG-42",
        created_by="fingerprint",
        ttl_seconds=600,
    )
    resolution = await store.resolve(
        project_id="alpha",
        tokens=[grant.token],
        requested_tools=["payment.create", "weather.read"],
        purpose="billing",
        request_id="ygm_one",
        consume=True,
    )
    assert resolution.approved_tools == ["payment.create"]
    assert resolution.approval_ids == [grant.id]

    with pytest.raises(ApprovalError, match="already been consumed"):
        await store.resolve(
            project_id="alpha",
            tokens=[grant.token],
            requested_tools=["payment.create"],
            purpose="billing",
            request_id="ygm_two",
            consume=True,
        )


@pytest.mark.asyncio
async def test_approval_capability_cannot_cross_project_or_purpose(fresh_db) -> None:
    store = ApprovalStore()
    grant = await store.create(
        project_id="alpha",
        tools=["sql.execute"],
        purpose="reporting",
        ticket=None,
        created_by=None,
        ttl_seconds=600,
    )
    with pytest.raises(ApprovalError, match="this project"):
        await store.resolve(
            project_id="beta",
            tokens=[grant.token],
            requested_tools=["sql.execute"],
            purpose="reporting",
            request_id="ygm_cross_project",
            consume=False,
        )
    with pytest.raises(ApprovalError, match="purpose"):
        await store.resolve(
            project_id="alpha",
            tokens=[grant.token],
            requested_tools=["sql.execute"],
            purpose="administration",
            request_id="ygm_wrong_purpose",
            consume=False,
        )


@pytest.mark.asyncio
async def test_approval_patterns_can_authorize_server_managed_tools(fresh_db) -> None:
    store = ApprovalStore()
    grant = await store.create(
        project_id="alpha",
        tools=["mcp.finance.*"],
        purpose="billing",
        ticket=None,
        created_by=None,
        ttl_seconds=600,
    )
    resolution = await store.resolve(
        project_id="alpha",
        tokens=[grant.token],
        requested_tools=[],
        purpose="billing",
        request_id="ygm_tool_loop",
        consume=False,
    )
    assert resolution.approved_tools == ["mcp.finance.*"]
