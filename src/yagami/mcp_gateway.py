"""Authenticated MCP server facade over the governed Yagami gateway."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.tools import Tool
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase, FuncMetadata
from pydantic import create_model
from starlette.responses import JSONResponse
from starlette.routing import Route

from .auth import Authenticator, Principal
from .backends.base import Message
from .capabilities import runtime_capabilities
from .gateway import GatewayRequestOptions, GatewayService
from .governance import inspect_context, inspect_output
from .policy import PolicyContext, PolicyMode, RoutePolicy
from .router.schema import Sensitivity
from .skills.base import SkillContext
from .skills.mcp_manager import McpManager

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


def _safe_tool_error(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"code": code, "message": message, "retryable": retryable},
    }


async def invoke_downstream_tool(
    gateway: GatewayService,
    manager: McpManager,
    principal: Principal,
    *,
    tool_identity: str,
    arguments: dict[str, Any],
    purpose: str = "general",
    sensitivity: str = "none",
    approval_token: str | None = None,
) -> dict[str, Any]:
    """Authorize and execute one downstream MCP tool without a model hop."""

    _require_scope(principal, "gateway:invoke")
    if not purpose or len(purpose) > 64:
        return _safe_tool_error("invalid_purpose", "purpose must contain 1 to 64 characters")
    try:
        sensitivity_hint = Sensitivity(sensitivity)
    except ValueError:
        return _safe_tool_error(
            "invalid_sensitivity",
            "sensitivity must be none, phi, phi_medical, or secret",
        )
    tool = manager.get_tool(tool_identity)
    if tool is None:
        return _safe_tool_error(
            "mcp_tool_unavailable",
            "the capability is unavailable or quarantined",
            retryable=True,
        )

    request_id = "ygm_mcp_" + uuid4().hex
    context = PolicyContext(
        project_id=principal.project_id,
        subject_id=principal.subject_id or principal.key_fingerprint,
        purpose=purpose,
        sensitivity_hint=sensitivity_hint,
        requested_tools=[tool_identity],
        approval_tokens=[approval_token] if approval_token else [],
    )
    serialized_arguments = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    argument_inspection = inspect_output(serialized_arguments)
    injection = inspect_context(serialized_arguments)
    schema = {
        "type": "function",
        "function": {
            "name": tool_identity,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }
    checks = await gateway.tool_schemas.inspect(
        project_id=principal.project_id,
        tools=[schema],
        pin_missing=True,
    )
    if checks[0].status == "drift":
        await gateway.append_audit(
            project_id=principal.project_id,
            request_id=request_id,
            event_type="mcp.tool_denied",
            payload={
                "reason_code": "mcp_schema_drift",
                "schema_hash": checks[0].schema_hash,
            },
        )
        return _safe_tool_error(
            "mcp_schema_drift",
            "the tool schema changed and requires administrator approval",
        )

    evaluation = gateway.policy_engine.evaluate(
        context=context,
        detected_sensitivity=argument_inspection.sensitivity,
        candidate_backend=tool_identity,
        candidate_trust_zone=tool.trust_zone,
    )
    evaluation.tool_schema_checks = [checks[0].summary()]
    approved_tools: list[str] = []
    approval_ids: list[str] = []
    if approval_token:
        try:
            resolution = await gateway.approvals.resolve(
                project_id=principal.project_id,
                tokens=[approval_token],
                requested_tools=[tool_identity],
                purpose=purpose,
                request_id=request_id,
                consume=False,
                subject_id=context.subject_id,
                schema_hash=checks[0].schema_hash,
            )
        except Exception:  # approval details are deliberately not reflected
            return _safe_tool_error(
                "invalid_tool_approval",
                "the one-time approval is invalid, expired, consumed, or out of scope",
            )
        approved_tools = resolution.approved_tools
        approval_ids = resolution.approval_ids
    context = context.model_copy(
        update={"approved_tools": approved_tools, "approval_ids": approval_ids}
    )
    gateway._enforce_tool_policy(context, evaluation)
    if tool.risk_level in {"high", "critical"} and tool_identity not in approved_tools:
        evaluation.denied = True
        evaluation.reasons.append("tool risk level requires a one-time approval")
    if (
        evaluation.allowed_trust_zones is not None
        and tool.trust_zone not in evaluation.allowed_trust_zones
    ):
        evaluation.denied = True
        evaluation.reasons.append("tool trust zone is outside the policy ceiling")
    if evaluation.route == RoutePolicy.DENY:
        evaluation.denied = True
    if injection.suspicious:
        evaluation.denied = True
        evaluation.context_risk = injection.summary()
        evaluation.reasons.append("tool arguments were quarantined by the context firewall")
    if evaluation.denied and evaluation.mode == PolicyMode.ENFORCE:
        reason_code = (
            "tool_argument_injection" if injection.suspicious else "mcp_tool_policy_denied"
        )
        await gateway.append_audit(
            project_id=principal.project_id,
            request_id=request_id,
            event_type="mcp.tool_denied",
            payload={
                "reason_code": reason_code,
                "policy_hash": evaluation.policy_hash,
                "schema_hash": checks[0].schema_hash,
                "approval_ids": approval_ids,
            },
        )
        return _safe_tool_error(reason_code, "the tool call was denied by Yagami policy")

    if approval_token:
        try:
            await gateway.approvals.resolve(
                project_id=principal.project_id,
                tokens=[approval_token],
                requested_tools=[tool_identity],
                purpose=purpose,
                request_id=request_id,
                consume=True,
                subject_id=context.subject_id,
                schema_hash=checks[0].schema_hash,
            )
        except Exception:
            return _safe_tool_error(
                "invalid_tool_approval",
                "the one-time approval could not be consumed",
            )
    result = await manager.call_tool(
        tool_identity,
        arguments,
        SkillContext(
            session_id=request_id,
            session_sensitivity=evaluation.effective_sensitivity,
            project_id=principal.project_id,
            purpose=purpose,
            subject_id=principal.subject_id or principal.key_fingerprint,
        ),
    )
    result_risk = inspect_context(result.content) if result.ok else None
    if result.ok and result_risk is not None and result_risk.suspicious:
        result = result.__class__(
            ok=False,
            error="downstream content was quarantined",
            artifacts={
                **result.artifacts,
                "error_code": "mcp_result_injection",
                "context_risk": result_risk.summary(),
            },
        )
    await gateway.append_audit(
        project_id=principal.project_id,
        request_id=request_id,
        event_type="mcp.tool_executed" if result.ok else "mcp.tool_failed",
        payload={
            "outcome": "ok" if result.ok else "error",
            "error_code": result.artifacts.get("error_code"),
            "policy_hash": evaluation.policy_hash,
            "schema_hash": checks[0].schema_hash,
            "approval_ids": approval_ids,
            "trust_zone": tool.trust_zone.value,
        },
    )
    if not result.ok:
        return _safe_tool_error(
            str(result.artifacts.get("error_code") or "mcp_tool_failed"),
            result.error or "the downstream tool failed",
            retryable=bool(result.artifacts.get("retryable")),
        )
    return {
        "ok": True,
        "content": result.content,
        "evidence": {
            "request_id": request_id,
            "policy_hash": evaluation.policy_hash,
            "schema_hash": checks[0].schema_hash,
            "trust_zone": tool.trust_zone.value,
            "approval_ids": approval_ids,
        },
    }


def _native_tool_name(identity: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "__", identity).strip("_") or "mcp_tool"
    if len(base) <= 96:
        return base
    return f"{base[:81]}_{hashlib.sha256(identity.encode()).hexdigest()[:14]}"


def register_downstream_tools(
    server: FastMCP,
    gateway: GatewayService,
    manager: McpManager,
) -> list[str]:
    """Expose pinned downstream schemas as native MCP tools.

    FastMCP does not currently provide a public dynamic-schema registration API,
    so this adapter constructs the SDK's documented Tool model while retaining
    FastMCP's normal call validation and dispatch.
    """

    registered: list[str] = []
    for row in manager.status():
        identity = str(row["identity"])
        downstream = manager.get_tool(identity)
        if downstream is None:
            continue
        native_name = _native_tool_name(identity)
        properties = downstream.input_schema.get("properties", {})
        required = set(downstream.input_schema.get("required", []))
        fields = {
            str(name): (Any, ... if name in required else None)
            for name in properties
            if isinstance(name, str)
        }
        arg_model = create_model(
            f"McpArgs_{hashlib.sha256(identity.encode()).hexdigest()[:12]}",
            __base__=ArgModelBase,
            **fields,
        )  # type: ignore[call-overload]

        async def proxy(
            _identity: str = identity,
            **kwargs: Any,
        ) -> dict[str, Any]:
            return await invoke_downstream_tool(
                gateway,
                manager,
                _current_principal(),
                tool_identity=_identity,
                arguments=kwargs,
            )

        tool = Tool(
            fn=proxy,
            name=native_name,
            title=None,
            description=(
                f"Governed downstream MCP capability. Stable identity: {identity}. "
                f"Trust zone: {downstream.trust_zone.value}; risk: {downstream.risk_level}. "
                f"{downstream.description}"
            ),
            parameters=downstream.input_schema,
            fn_metadata=FuncMetadata(arg_model=arg_model),
            is_async=True,
            context_kwarg=None,
            annotations=None,
            meta={
                "yagami/identity": identity,
                "yagami/schemaHash": downstream.schema_hash,
                "yagami/trustZone": downstream.trust_zone.value,
                "yagami/riskLevel": downstream.risk_level,
            },
        )
        server._tool_manager._tools[native_name] = tool
        registered.append(native_name)
    return registered


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

    @server.tool(
        name="yagami_capabilities",
        description="List governed downstream MCP tools, resources, prompts, and quarantines.",
        structured_output=True,
    )
    async def yagami_capabilities() -> dict[str, Any]:
        principal = _current_principal()
        _require_scope(principal, "gateway:invoke")
        from .skills.mcp_manager import get_manager

        manager = get_manager()
        catalog = (
            manager.catalog_for_subject(
                project_id=principal.project_id,
                subject_id=principal.subject_id or principal.key_fingerprint or "anonymous",
            )
            if manager is not None
            else {"tools": [], "resources": [], "prompts": [], "quarantined": []}
        )
        return {
            "runtime": runtime_capabilities(backends=gateway.backends),
            "downstream": catalog,
        }

    @server.tool(
        name="yagami_execute_tool",
        description=(
            "Execute a namespaced downstream MCP tool through policy, schema, trust, "
            "approval, and context-firewall controls."
        ),
        structured_output=True,
    )
    async def yagami_execute_tool(
        tool_identity: str,
        arguments: dict[str, Any],
        purpose: str = "general",
        sensitivity: str = "none",
        approval_token: str | None = None,
    ) -> dict[str, Any]:
        from .skills.mcp_manager import get_manager

        manager = get_manager()
        if manager is None:
            return _safe_tool_error(
                "mcp_unavailable", "no downstream MCP servers are connected", retryable=True
            )
        return await invoke_downstream_tool(
            gateway,
            manager,
            _current_principal(),
            tool_identity=tool_identity,
            arguments=arguments,
            purpose=purpose,
            sensitivity=sensitivity,
            approval_token=approval_token,
        )

    @server.tool(
        name="yagami_read_resource",
        description="Read a namespaced downstream MCP resource through injection quarantine.",
        structured_output=True,
    )
    async def yagami_read_resource(resource_identity: str) -> dict[str, Any]:
        principal = _current_principal()
        _require_scope(principal, "gateway:invoke")
        from .skills.mcp_manager import get_manager

        manager = get_manager()
        if manager is None:
            return _safe_tool_error("mcp_unavailable", "no MCP servers are connected")
        result = await manager.read_resource(
            resource_identity,
            SkillContext(
                session_id="ygm_mcp_" + uuid4().hex,
                project_id=principal.project_id,
                subject_id=principal.subject_id or principal.key_fingerprint,
            ),
        )
        risk = inspect_context(result.content) if result.ok else None
        if risk is not None and risk.suspicious:
            result = result.__class__(
                ok=False,
                error="downstream resource content was quarantined",
                artifacts={
                    **result.artifacts,
                    "error_code": "mcp_resource_injection",
                    "context_risk": risk.summary(),
                },
            )
        await gateway.append_audit(
            project_id=principal.project_id,
            event_type="mcp.resource_read" if result.ok else "mcp.resource_denied",
            payload={
                "outcome": "ok" if result.ok else "error",
                "error_code": result.artifacts.get("error_code"),
            },
        )
        if not result.ok:
            return _safe_tool_error(
                str(result.artifacts.get("error_code") or "mcp_resource_failed"),
                result.error or "resource read failed",
                retryable=bool(result.artifacts.get("retryable")),
            )
        return {"ok": True, "content": result.content, "evidence": result.artifacts}

    @server.tool(
        name="yagami_get_prompt",
        description="Get a namespaced downstream MCP prompt through injection quarantine.",
        structured_output=True,
    )
    async def yagami_get_prompt(
        prompt_identity: str, arguments: dict[str, str] | None = None
    ) -> dict[str, Any]:
        principal = _current_principal()
        _require_scope(principal, "gateway:invoke")
        from .skills.mcp_manager import get_manager

        manager = get_manager()
        if manager is None:
            return _safe_tool_error("mcp_unavailable", "no MCP servers are connected")
        result = await manager.get_prompt(
            prompt_identity,
            arguments or {},
            SkillContext(
                session_id="ygm_mcp_" + uuid4().hex,
                project_id=principal.project_id,
                subject_id=principal.subject_id or principal.key_fingerprint,
            ),
        )
        risk = inspect_context(result.content) if result.ok else None
        if risk is not None and risk.suspicious:
            result = result.__class__(
                ok=False,
                error="downstream prompt content was quarantined",
                artifacts={
                    **result.artifacts,
                    "error_code": "mcp_prompt_injection",
                    "context_risk": risk.summary(),
                },
            )
        await gateway.append_audit(
            project_id=principal.project_id,
            event_type="mcp.prompt_read" if result.ok else "mcp.prompt_denied",
            payload={
                "outcome": "ok" if result.ok else "error",
                "error_code": result.artifacts.get("error_code"),
            },
        )
        if not result.ok:
            return _safe_tool_error(
                str(result.artifacts.get("error_code") or "mcp_prompt_failed"),
                result.error or "prompt retrieval failed",
                retryable=bool(result.artifacts.get("retryable")),
            )
        return {"ok": True, "content": result.content, "evidence": result.artifacts}

    http_app = server.streamable_http_app()
    route = http_app.routes[0]
    if not isinstance(route, Route):  # pragma: no cover - SDK contract guard
        raise RuntimeError("MCP SDK did not create a Streamable HTTP route")
    endpoint = McpBearerEndpoint(route.endpoint, authenticator)
    return server, http_app, endpoint
