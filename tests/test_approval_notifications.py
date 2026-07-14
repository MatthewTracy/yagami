from __future__ import annotations

import json

import httpx
import pytest

from yagami.governance.approval_notifications import ApprovalNotifier
from yagami.governance.approvals import ApprovalStore


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["json", "slack", "teams"])
async def test_approval_notifier_supports_enterprise_webhook_formats(format) -> None:
    delivered: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        delivered.append(json.loads(request.content))
        return httpx.Response(200)

    notifier = ApprovalNotifier(
        "https://notifications.example.test/yagami",
        format=format,
        transport=httpx.MockTransport(handler),
    )

    await notifier.notify(
        "tool_approval.created",
        {
            "approval_id": "apr_test",
            "project_id": "alpha",
            "tools": ["email.send"],
            "purpose": "support",
            "expires_at": 123,
            "token": "must-never-leave-yagami",
            "prompt": "also private",
        },
    )

    serialized = json.dumps(delivered)
    assert "apr_test" in serialized
    assert "must-never-leave-yagami" not in serialized
    assert "also private" not in serialized


def test_approval_notifier_requires_secure_remote_transport() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        ApprovalNotifier("http://hooks.example.test/yagami")


@pytest.mark.asyncio
async def test_approval_store_notifies_without_exposing_capability_token(fresh_db) -> None:
    events: list[tuple[str, dict]] = []

    class RecordingNotifier:
        async def notify(self, event: str, details: dict) -> None:
            events.append((event, details))

    store = ApprovalStore(RecordingNotifier())
    grant = await store.create(
        project_id="alpha",
        tools=["email.send"],
        purpose="support",
        ticket="SEC-123",
        created_by="approver",
        ttl_seconds=900,
    )
    await store.revoke(project_id="alpha", approval_id=grant.id)

    assert [event for event, _ in events] == [
        "tool_approval.created",
        "tool_approval.revoked",
    ]
    assert grant.token not in json.dumps(events)
    assert "SEC-123" not in json.dumps(events)
