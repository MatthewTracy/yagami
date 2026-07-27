from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from yagami.admin_audit import AdminAuditMiddleware


class RecordingGateway:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[dict] = []

    async def append_audit(self, **event) -> None:
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.events.append(event)


def _audit_app(gateway: RecordingGateway, *, required: bool) -> FastAPI:
    app = FastAPI()
    app.state.runtime = SimpleNamespace(
        gateway=gateway,
        audit=SimpleNamespace(required=required),
    )
    app.add_middleware(AdminAuditMiddleware)

    @app.post("/api/settings", name="update-settings")
    async def update_settings(request: Request) -> dict:
        request.state.admin_principal = SimpleNamespace(
            project_id="project-one",
            key_fingerprint="sha256:actor",
            roles=frozenset({"admin", "security"}),
        )
        await request.json()
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_admin_audit_is_content_free() -> None:
    gateway = RecordingGateway()
    app = _audit_app(gateway, required=True)
    transport = ASGITransport(app=app)
    body = {
        "prompt": "patient Alice lives at 10 Main Street",
        "api_key": "sk-super-secret-value",
        "tool_arguments": {"recipient": "alice@example.com"},
    }
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/settings?customer=Alice", json=body)

    assert response.status_code == 200
    assert len(gateway.events) == 1
    event = gateway.events[0]
    assert event["project_id"] == "project-one"
    assert event["event_type"] == "admin.change"
    assert event["payload"] == {
        "operation": "update-settings",
        "method": "POST",
        "status_code": 200,
        "actor_fingerprint": "sha256:actor",
        "actor_roles": ["admin", "security"],
    }
    encoded = repr(event)
    for forbidden in ("Alice", "Main Street", "sk-super", "alice@example.com", "customer"):
        assert forbidden not in encoded


@pytest.mark.asyncio
async def test_required_admin_audit_fails_closed() -> None:
    app = _audit_app(RecordingGateway(fail=True), required=True)
    transport = ASGITransport(app=app)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/settings", json={"prompt": "do not log me"})
