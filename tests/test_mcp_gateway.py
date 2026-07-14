from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from starlette.responses import JSONResponse

from yagami.auth import Authenticator, Principal
from yagami.config import Settings
from yagami.gateway import GatewayService
from yagami.mcp_gateway import (
    McpBearerEndpoint,
    _current_principal,
    build_mcp_server,
    invoke_chat,
)


class FakeGateway:
    def __init__(self) -> None:
        self.prepared: dict[str, Any] | None = None

    async def prepare(self, **kwargs):
        self.prepared = kwargs
        return SimpleNamespace(marker="prepared")

    async def execute(self, prepared):
        assert prepared.marker == "prepared"
        policy = SimpleNamespace(passport=lambda: {"route": "local"})
        return SimpleNamespace(
            text="governed output",
            request_id="ygm_test",
            backend="echo",
            policy=policy,
        )


@pytest.mark.asyncio
async def test_mcp_chat_inherits_authenticated_project_identity() -> None:
    gateway = FakeGateway()
    principal = Principal(
        project_id="alpha",
        key_fingerprint="abc",
        authenticated=True,
        subject_id="developer-one",
        roles=frozenset({"service"}),
        scopes=frozenset({"gateway:invoke"}),
    )

    result = await invoke_chat(
        cast(GatewayService, gateway),
        principal,
        input_text="hello",
        purpose="engineering",
        sensitivity="secret",
    )

    assert result["output"] == "governed output"
    assert result["policy"] == {"route": "local"}
    assert gateway.prepared is not None
    context = gateway.prepared["context"]
    assert context.project_id == "alpha"
    assert context.subject_id == "developer-one"
    assert context.sensitivity_hint.value == "secret"


@pytest.mark.asyncio
async def test_mcp_chat_requires_invoke_scope() -> None:
    principal = Principal(
        project_id="alpha",
        key_fingerprint="abc",
        authenticated=True,
        roles=frozenset({"service"}),
        scopes=frozenset({"policy:preview"}),
    )

    with pytest.raises(PermissionError, match="gateway:invoke"):
        await invoke_chat(cast(GatewayService, FakeGateway()), principal, input_text="hello")


@pytest.mark.asyncio
async def test_mcp_endpoint_authenticates_bearer_and_binds_context() -> None:
    settings = Settings(
        _env_file=None,
        YAGAMI_REQUIRE_AUTH=True,
        YAGAMI_API_KEYS=('{"alpha":{"key":"mcp-test-key-0123456789","scopes":["gateway:invoke"]}}'),
    )
    authenticator = Authenticator(settings)

    async def inner(scope, receive, send):
        principal = _current_principal()
        await JSONResponse({"project": principal.project_id})(scope, receive, send)

    endpoint = McpBearerEndpoint(inner, authenticator)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=endpoint), base_url="http://test"
    ) as client:
        denied = await client.post("/mcp")
        accepted = await client.post(
            "/mcp", headers={"Authorization": "Bearer mcp-test-key-0123456789"}
        )

    assert denied.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json() == {"project": "alpha"}


@pytest.mark.asyncio
async def test_mcp_server_advertises_governed_tools() -> None:
    settings = Settings(_env_file=None)
    server, http_app, endpoint = build_mcp_server(
        cast(GatewayService, FakeGateway()), Authenticator(settings)
    )

    tools = await server.list_tools()

    assert {tool.name for tool in tools} == {"yagami_chat", "yagami_policy_preview"}
    assert http_app.routes
    assert isinstance(endpoint, McpBearerEndpoint)
