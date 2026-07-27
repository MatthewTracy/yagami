from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from mcp.types import CallToolResult, TextContent
from starlette.responses import JSONResponse

from yagami.auth import Authenticator, Principal
from yagami.config import Settings
from yagami.gateway import GatewayService
from yagami.governance import ApprovalStore, ToolSchemaRegistry
from yagami.mcp_gateway import (
    McpBearerEndpoint,
    _current_principal,
    build_mcp_server,
    invoke_chat,
    invoke_downstream_tool,
    register_downstream_tools,
)
from yagami.policy import PolicyEngine
from yagami.skills.mcp_manager import McpManager, _McpTool


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

    assert {tool.name for tool in tools} == {
        "yagami_chat",
        "yagami_policy_preview",
        "yagami_capabilities",
        "yagami_execute_tool",
        "yagami_read_resource",
        "yagami_get_prompt",
    }
    assert http_app.routes
    assert isinstance(endpoint, McpBearerEndpoint)


class _ToolSession:
    async def call_tool(self, name, arguments, read_timeout_seconds=None):
        return CallToolResult(
            content=[TextContent(type="text", text=f"{name}:ok")],
            isError=False,
        )


class _PolicyGateway:
    def __init__(self, policy_path) -> None:
        self.tool_schemas = ToolSchemaRegistry()
        self.policy_engine = PolicyEngine(policy_path)
        self.approvals = ApprovalStore()
        self.events: list[tuple[str, dict]] = []

    def _enforce_tool_policy(self, context, evaluation) -> None:
        GatewayService._enforce_tool_policy(self, context, evaluation)

    async def append_audit(self, *, event_type, payload, **kwargs) -> None:
        self.events.append((event_type, payload))


@pytest.mark.asyncio
async def test_direct_mcp_tool_requires_schema_bound_approval(fresh_db, tmp_path) -> None:
    gateway = _PolicyGateway(tmp_path / "missing-policy.yaml")
    manager = McpManager()
    identity = "mcp.finance.transfer"
    manager._tools[identity] = _McpTool(
        server_name="finance",
        tool_name="transfer",
        description="Transfer approved funds",
        input_schema={
            "type": "object",
            "properties": {"amount": {"type": "number"}},
            "required": ["amount"],
        },
        session=_ToolSession(),
        risk_level="high",
    )
    principal = Principal(
        project_id="alpha",
        key_fingerprint="fingerprint",
        authenticated=True,
        subject_id="operator-7",
        scopes=frozenset({"gateway:invoke"}),
    )

    denied = await invoke_downstream_tool(
        gateway,
        manager,
        principal,
        tool_identity=identity,
        arguments={"amount": 12.5},
        purpose="billing",
    )
    pins = await gateway.tool_schemas.list(project_id="alpha")
    grant = await gateway.approvals.create(
        project_id="alpha",
        tools=[identity],
        subject_id="operator-7",
        schema_hash=pins[0]["pinned_hash"],
        purpose="billing",
        ticket="CHG-7",
        created_by="security",
        ttl_seconds=600,
    )
    accepted = await invoke_downstream_tool(
        gateway,
        manager,
        principal,
        tool_identity=identity,
        arguments={"amount": 12.5},
        purpose="billing",
        approval_token=grant.token,
    )
    replayed = await invoke_downstream_tool(
        gateway,
        manager,
        principal,
        tool_identity=identity,
        arguments={"amount": 12.5},
        purpose="billing",
        approval_token=grant.token,
    )

    assert denied["error"]["code"] == "mcp_tool_policy_denied"
    assert accepted["ok"] is True
    assert accepted["content"] == "transfer:ok"
    assert accepted["evidence"]["schema_hash"] == pins[0]["pinned_hash"]
    assert replayed["error"]["code"] == "invalid_tool_approval"
    assert all("amount" not in str(payload) for _event, payload in gateway.events)


@pytest.mark.asyncio
async def test_downstream_tools_are_native_and_namespaced() -> None:
    gateway = FakeGateway()
    server, _app, _endpoint = build_mcp_server(
        cast(GatewayService, gateway), Authenticator(Settings(_env_file=None))
    )
    manager = McpManager()
    identity = "mcp.echo.echo"
    manager._tools[identity] = _McpTool(
        server_name="echo",
        tool_name="echo",
        description="Echo text",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        session=_ToolSession(),
        schema_hash="sha256:" + "a" * 64,
    )

    names = register_downstream_tools(server, cast(GatewayService, gateway), manager)
    tools = await server.list_tools()
    exposed = next(tool for tool in tools if tool.name == names[0])

    assert names == ["mcp__echo__echo"]
    assert exposed.inputSchema["required"] == ["text"]
    assert exposed.meta["yagami/identity"] == identity
