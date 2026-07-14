"""Authenticated MCP server facade over the governed Yagami gateway."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Any

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.routing import Route

from .auth import Authenticator, Principal
from .backends.base import Message
from .gateway import GatewayRequestOptions, GatewayService
from .policy import PolicyContext
from .router.schema import Sensitivity

_principal: ContextVar[Principal | None] = ContextVar("yagami_mcp_principal", default=None)


def _bearer_token(headers: list[tuple[bytes, bytes]]) -> str | None:
    for raw_name, raw_value in headers:
        if raw_name.lower() != b"authorization":
            continue
        value = raw_value.decode("latin-1")
        scheme, separator, token = value.partition(" ")
        if separator and scheme.casefold() == "bearer" and token:
            return token
    return None


def _require_scope(principal: Principal, scope: str) -> None:
    if scope not in principal.scopes and "local-admin" not in principal.roles:
        raise PermissionError(f"identity lacks required scope {scope!r}")


class McpBearerEndpoint:
    """Authenticate every MCP transport request and bind its project identity."""

    def __init__(self, app: Any, authenticator: Authenticator) -> None:
        self.app = app
        self.authenticator = authenticator

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            principal = await asyncio.to_thread(
                self.authenticator.authenticate, _bearer_token(scope.get("headers", []))
            )
        except HTTPException as exc:
            response = JSONResponse(
                {"jsonrpc": "2.0", "error": {"code": -32001, "message": exc.detail}},
                status_code=exc.status_code,
                headers=exc.headers,
            )
            await response(scope, receive, send)
            return
        token = _principal.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            _principal.reset(token)


def _current_principal() -> Principal:
    principal = _principal.get()
    if principal is None:
        raise RuntimeError("MCP request identity is unavailable")
    return principal


def _validate_input(input_text: str, purpose: str, max_tokens: int) -> None:
    if not input_text or len(input_text) > 1_000_000:
        raise ValueError("input must contain between 1 and 1,000,000 characters")
    if not purpose or len(purpose) > 64:
        raise ValueError("purpose must contain between 1 and 64 characters")
    if not 1 <= max_tokens <= 131_072:
        raise ValueError("max_tokens must be between 1 and 131072")


async def invoke_chat(
    gateway: GatewayService,
    principal: Principal,
    *,
    input_text: str,
    model: str = "yagami-auto",
    purpose: str = "general",
    sensitivity: str = "none",
    max_tokens: int = 2048,
) -> dict[str, Any]:
    _require_scope(principal, "gateway:invoke")
    _validate_input(input_text, purpose, max_tokens)
    if len(model) > 128:
        raise ValueError("model is limited to 128 characters")
    try:
        sensitivity_hint = Sensitivity(sensitivity)
    except ValueError as exc:
        raise ValueError("sensitivity must be none, phi, phi_medical, or secret") from exc
    context = PolicyContext(
        project_id=principal.project_id,
        subject_id=principal.subject_id,
        purpose=purpose,
        sensitivity_hint=sensitivity_hint,
    )
    prepared = await gateway.prepare(
        messages=[Message(role="user", content=input_text)],
        model=model,
        context=context,
        options=GatewayRequestOptions(max_tokens=max_tokens),
    )
    result = await gateway.execute(prepared)
    return {
        "output": result.text,
        "request_id": result.request_id,
        "backend": result.backend,
        "policy": result.policy.passport(),
    }


async def preview_policy(
    gateway: GatewayService,
    principal: Principal,
    *,
    input_text: str,
    model: str = "yagami-auto",
    purpose: str = "general",
    sensitivity: str = "none",
) -> dict[str, Any]:
    _require_scope(principal, "policy:preview")
    _validate_input(input_text, purpose, 1)
    try:
        sensitivity_hint = Sensitivity(sensitivity)
    except ValueError as exc:
        raise ValueError("sensitivity must be none, phi, phi_medical, or secret") from exc
    prepared = await gateway.prepare(
        messages=[Message(role="user", content=input_text)],
        model=model,
        context=PolicyContext(
            project_id=principal.project_id,
            subject_id=principal.subject_id,
            purpose=purpose,
            sensitivity_hint=sensitivity_hint,
        ),
        options=GatewayRequestOptions(max_tokens=1),
        persist=False,
        raise_on_deny=False,
    )
    return prepared.policy.passport()


def build_mcp_server(
    gateway: GatewayService, authenticator: Authenticator
) -> tuple[FastMCP, Any, McpBearerEndpoint]:
    server = FastMCP(
        "Yagami",
        instructions=(
            "Use Yagami to run model requests through project-scoped privacy, routing, "
            "retention, and tool policies. Treat policy passports as authoritative."
        ),
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    @server.tool(
        name="yagami_chat",
        description="Run a model request through Yagami's governed context firewall.",
        structured_output=True,
    )
    async def yagami_chat(
        input: str,
        model: str = "yagami-auto",
        purpose: str = "general",
        sensitivity: str = "none",
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        return await invoke_chat(
            gateway,
            _current_principal(),
            input_text=input,
            model=model,
            purpose=purpose,
            sensitivity=sensitivity,
            max_tokens=max_tokens,
        )

    @server.tool(
        name="yagami_policy_preview",
        description="Preview the content-free policy passport without generating output.",
        structured_output=True,
    )
    async def yagami_policy_preview(
        input: str,
        model: str = "yagami-auto",
        purpose: str = "general",
        sensitivity: str = "none",
    ) -> dict[str, Any]:
        return await preview_policy(
            gateway,
            _current_principal(),
            input_text=input,
            model=model,
            purpose=purpose,
            sensitivity=sensitivity,
        )

    http_app = server.streamable_http_app()
    route = http_app.routes[0]
    if not isinstance(route, Route):  # pragma: no cover - SDK contract guard
        raise RuntimeError("MCP SDK did not create a Streamable HTTP route")
    endpoint = McpBearerEndpoint(route.endpoint, authenticator)
    return server, http_app, endpoint
