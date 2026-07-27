"""Governed downstream MCP aggregation.

Each downstream capability receives a stable ``mcp.<server>.<name>`` identity.
The manager owns connection health, schema pinning, drift quarantine, bounded
retries, and trust metadata. It feeds the same Skill registry used by managed
agent tool loops and the public Yagami MCP facade.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx
from jsonschema import ValidationError, validate
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from ..backends.base import TrustZone
from ..config import McpServerConfig
from ..governance import ToolSchemaRegistry, inspect_context
from ..router.schema import Sensitivity
from .base import Skill, SkillContext, SkillResult
from .mcp_auth import (
    OAuthClientCredentialsAuth,
    validate_remote_destination,
)

log = logging.getLogger("yagami.skills.mcp")
_SAFE_SERVER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


@dataclass
class _McpTool:
    server_name: str
    tool_name: str
    description: str
    input_schema: dict
    session: Any
    transport: str = "stdio"
    auth: str = "none"
    trust_zone: TrustZone = TrustZone.DEVICE
    data_ceiling: Sensitivity = Sensitivity.NONE
    risk_level: str = "medium"
    schema_hash: str = ""
    schema_status: str = "unmanaged"
    manager: McpManager | None = field(default=None, repr=False)
    call_timeout_seconds: float = 60.0

    @property
    def identity(self) -> str:
        return f"mcp.{self.server_name}.{self.tool_name}"


@dataclass
class _ServerHealth:
    state: str = "disconnected"
    connected_at: float | None = None
    last_success_at: float | None = None
    last_failure_at: float | None = None
    consecutive_failures: int = 0
    circuit_open_until: float = 0.0
    last_error_code: str | None = None

    def success(self) -> None:
        now = time.time()
        self.state = "healthy"
        self.last_success_at = now
        self.consecutive_failures = 0
        self.circuit_open_until = 0.0
        self.last_error_code = None

    def failure(self, code: str) -> None:
        self.state = "unhealthy"
        self.last_failure_at = time.time()
        self.consecutive_failures += 1
        self.last_error_code = code
        if self.consecutive_failures >= 3:
            self.state = "circuit_open"
            self.circuit_open_until = time.monotonic() + min(
                60.0, float(2 ** min(self.consecutive_failures, 6))
            )


class McpSkillAdapter:
    """Wrap one downstream MCP tool in Yagami's provider-neutral Skill protocol."""

    def __init__(self, tool: _McpTool) -> None:
        self._tool = tool
        self.name = tool.identity
        self.description = f"[MCP:{tool.server_name}] {tool.description or tool.tool_name}"
        self.input_schema = tool.input_schema or {"type": "object", "properties": {}}
        self.requires_network = tool.transport == "streamable_http"
        self.sensitivity_ceiling = tool.data_ceiling
        self.trust_zone = tool.trust_zone
        self.risk_level = tool.risk_level
        self.requires_approval = tool.risk_level in {"high", "critical"}
        self.schema_hash = tool.schema_hash

    async def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        if self._tool.manager is not None:
            return await self._tool.manager.call_tool(self.name, args, ctx)
        return await _invoke_tool(self._tool, args)


async def _invoke_tool(tool: _McpTool, args: dict) -> SkillResult:
    try:
        validate(instance=args, schema=tool.input_schema)
    except ValidationError as exc:
        return SkillResult(
            ok=False,
            error=f"mcp arguments failed the pinned schema: {exc.message}",
            artifacts={"schema_hash": tool.schema_hash, "schema_invalid": True},
        )
    try:
        result = await tool.session.call_tool(
            tool.tool_name,
            args,
            read_timeout_seconds=timedelta(seconds=tool.call_timeout_seconds),
        )
    except Exception as exc:  # noqa: BLE001 - adapters always surface structured failure
        return SkillResult(
            ok=False,
            error=f"mcp transport unavailable: {type(exc).__name__}",
            artifacts={"retryable": True, "error_code": "mcp_transport_unavailable"},
        )
    text_parts = [
        text for block in result.content if isinstance((text := getattr(block, "text", None)), str)
    ]
    content = "\n".join(text_parts)
    structured = getattr(result, "structuredContent", None)
    if not content and structured is not None:
        content = json.dumps(structured, sort_keys=True, separators=(",", ":"))
    if result.isError:
        return SkillResult(
            ok=False,
            error=content or "MCP tool reported an error",
            artifacts={"error_code": "mcp_tool_error", "schema_hash": tool.schema_hash},
        )
    return SkillResult(
        ok=True,
        content=content,
        artifacts={
            "mcp_server": tool.server_name,
            "tool_identity": tool.identity,
            "trust_zone": tool.trust_zone.value,
            "risk_level": tool.risk_level,
            "schema_hash": tool.schema_hash,
        },
    )


class McpManager:
    """Own live downstream MCP connections and their governed capability catalog."""

    def __init__(
        self,
        *,
        schema_registry: ToolSchemaRegistry | None = None,
        schema_project_id: str = "local",
    ) -> None:
        self._configs: dict[str, McpServerConfig] = {}
        self._stacks: dict[str, AsyncExitStack] = {}
        self._sessions: dict[str, ClientSession] = {}
        self._tools: dict[str, _McpTool] = {}
        self._resources: dict[str, dict[str, Any]] = {}
        self._prompts: dict[str, dict[str, Any]] = {}
        self._quarantined: dict[str, dict[str, Any]] = {}
        self._health: dict[str, _ServerHealth] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._schema_registry = schema_registry
        self._schema_project_id = schema_project_id
        self._closing = False

    async def connect_all(self, servers: dict[str, McpServerConfig]) -> None:
        for name, server_cfg in servers.items():
            if not _SAFE_SERVER_NAME.fullmatch(name):
                log.warning("mcp server %r has an unsafe identity and was skipped", name)
                continue
            self._configs[name] = server_cfg
            self._health[name] = _ServerHealth(state="connecting")
            self._locks[name] = asyncio.Lock()
            try:
                await self._connect_one(name, server_cfg)
            except Exception as exc:  # noqa: BLE001 - one server cannot prevent startup
                self._health[name].failure(_error_code(exc))
                log.warning("mcp server %r failed to connect: %s", name, type(exc).__name__)

    async def _connect_one(self, name: str, server_cfg: McpServerConfig) -> None:
        stack = AsyncExitStack()
        try:
            if server_cfg.transport == "streamable_http":
                session = await self._open_http(stack, name, server_cfg)
            else:
                params = StdioServerParameters(
                    command=server_cfg.command,
                    args=server_cfg.args,
                    env={**os.environ, **server_cfg.env},
                )
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
            listed = await session.list_tools()
            tools = await self._prepare_tools(name, server_cfg, session, listed.tools)
            resources, prompts = await self._discover_non_tool_capabilities(
                name, server_cfg, session
            )
        except BaseException:
            await stack.aclose()
            raise

        old_stack = self._stacks.pop(name, None)
        if old_stack is not None:
            await old_stack.aclose()
        self._stacks[name] = stack
        self._sessions[name] = session
        self._tools = {key: tool for key, tool in self._tools.items() if tool.server_name != name}
        self._resources = {
            key: item for key, item in self._resources.items() if item["server"] != name
        }
        self._prompts = {key: item for key, item in self._prompts.items() if item["server"] != name}
        self._tools.update({tool.identity: tool for tool in tools})
        self._resources.update(resources)
        self._prompts.update(prompts)
        health = self._health.setdefault(name, _ServerHealth())
        health.connected_at = time.time()
        health.success()
        log.info(
            "mcp server %r connected: %d tool(s), %d resource(s), %d prompt(s)",
            name,
            len(tools),
            len(resources),
            len(prompts),
        )

    async def _open_http(
        self,
        stack: AsyncExitStack,
        name: str,
        server_cfg: McpServerConfig,
    ) -> ClientSession:
        trust_zone = server_cfg.trust_zone or TrustZone.EXTERNAL
        url = await validate_remote_destination(
            server_cfg.url,
            field=f"mcp_servers.{name}.url",
            trust_zone=trust_zone,
            allowed_hosts=server_cfg.allowed_hosts,
            allow_private_addresses=server_cfg.allow_private_addresses,
        )
        headers: dict[str, str] = {}
        auth: httpx.Auth | None = None
        oauth_auth: OAuthClientCredentialsAuth | None = None
        if server_cfg.auth == "bearer_env":
            token = os.getenv(server_cfg.bearer_token_env, "")
            if not token:
                raise ValueError(f"MCP server {name!r} token environment variable is empty")
            headers["Authorization"] = "Bearer " + token
        elif server_cfg.auth == "client_credentials":
            client_id = os.getenv(server_cfg.oauth_client_id_env, "")
            client_secret = os.getenv(server_cfg.oauth_client_secret_env, "")
            if not client_id or not client_secret:
                raise ValueError(f"MCP server {name!r} OAuth credential variables are empty")
            oauth_auth = OAuthClientCredentialsAuth(
                token_url=server_cfg.oauth_token_url,
                client_id=client_id,
                client_secret=client_secret,
                scopes=server_cfg.oauth_scopes,
                resource=server_cfg.oauth_resource,
                token_endpoint_auth_method=server_cfg.oauth_token_endpoint_auth_method,
            )
            auth = oauth_auth

        async def guard_destination(request: httpx.Request) -> None:
            await validate_remote_destination(
                str(request.url),
                field=f"mcp_servers.{name}.request_url",
                trust_zone=trust_zone,
                allowed_hosts=server_cfg.allowed_hosts,
                allow_private_addresses=server_cfg.allow_private_addresses,
            )

        client = await stack.enter_async_context(
            httpx.AsyncClient(
                headers=headers,
                auth=auth,
                timeout=httpx.Timeout(server_cfg.call_timeout_seconds, connect=10.0),
                follow_redirects=False,
                event_hooks={"request": [guard_destination]},
            )
        )
        if oauth_auth is not None:
            stack.push_async_callback(oauth_auth.aclose)
        read, write, _get_session_id = await stack.enter_async_context(
            streamable_http_client(url, http_client=client)
        )
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    async def _prepare_tools(
        self,
        name: str,
        cfg: McpServerConfig,
        session: ClientSession,
        listed_tools: list[Any],
    ) -> list[_McpTool]:
        prepared: list[_McpTool] = []
        for listed in listed_tools:
            identity = f"mcp.{name}.{listed.name}"
            schema = listed.inputSchema or {"type": "object", "properties": {}}
            canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
            schema_hash = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
            description = listed.description or ""
            inspection = inspect_context(description)
            if inspection.suspicious:
                self._quarantined[identity] = {
                    "identity": identity,
                    "reason_code": "mcp_description_injection",
                    "signals": list(inspection.signals),
                    "schema_hash": schema_hash,
                }
                continue
            schema_status = "unmanaged"
            if self._schema_registry is not None:
                checks = await self._schema_registry.inspect(
                    project_id=self._schema_project_id,
                    tools=[
                        {
                            "type": "function",
                            "function": {
                                "name": identity,
                                "description": description,
                                "parameters": schema,
                            },
                        }
                    ],
                    pin_missing=True,
                )
                schema_status = checks[0].status
                if schema_status == "drift" and cfg.schema_drift == "block":
                    self._quarantined[identity] = {
                        "identity": identity,
                        "reason_code": "mcp_schema_drift",
                        "schema_hash": checks[0].schema_hash,
                    }
                    continue
            prepared.append(
                _McpTool(
                    server_name=name,
                    tool_name=listed.name,
                    description=description,
                    input_schema=schema,
                    session=session,
                    transport=cfg.transport,
                    auth=cfg.auth,
                    trust_zone=cfg.trust_zone or TrustZone.EXTERNAL,
                    data_ceiling=Sensitivity(cfg.data_ceiling),
                    risk_level=cfg.risk_level,
                    schema_hash=schema_hash,
                    schema_status=schema_status,
                    manager=self,
                    call_timeout_seconds=cfg.call_timeout_seconds,
                )
            )
        return prepared

    async def _discover_non_tool_capabilities(
        self,
        name: str,
        cfg: McpServerConfig,
        session: ClientSession,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        resources: dict[str, dict[str, Any]] = {}
        prompts: dict[str, dict[str, Any]] = {}
        try:
            listed_resources = await session.list_resources()
        except Exception:  # noqa: BLE001 - capability is optional
            listed_resources = None
        if listed_resources is not None:
            for resource in listed_resources.resources:
                uri = str(resource.uri)
                identity = f"mcp.{name}.resource.{hashlib.sha256(uri.encode()).hexdigest()[:16]}"
                resources[identity] = {
                    "identity": identity,
                    "server": name,
                    "uri": uri,
                    "name": resource.name or identity,
                    "trust_zone": (cfg.trust_zone or TrustZone.EXTERNAL).value,
                }
        try:
            listed_prompts = await session.list_prompts()
        except Exception:  # noqa: BLE001 - capability is optional
            listed_prompts = None
        if listed_prompts is not None:
            for prompt in listed_prompts.prompts:
                identity = f"mcp.{name}.prompt.{prompt.name}"
                prompts[identity] = {
                    "identity": identity,
                    "server": name,
                    "name": prompt.name,
                    "description": prompt.description or "",
                    "arguments": [
                        {
                            "name": argument.name,
                            "description": argument.description or "",
                            "required": bool(argument.required),
                        }
                        for argument in (prompt.arguments or [])
                    ],
                    "trust_zone": (cfg.trust_zone or TrustZone.EXTERNAL).value,
                }
        return resources, prompts

    async def call_tool(
        self,
        identity: str,
        args: dict,
        ctx: SkillContext | None = None,
    ) -> SkillResult:
        del ctx  # identity and sensitivity policy are enforced by the caller
        tool = self._tools.get(identity)
        if tool is None:
            return SkillResult(
                ok=False,
                error="MCP capability is unavailable or quarantined",
                artifacts={"error_code": "mcp_tool_unavailable"},
            )
        name = tool.server_name
        cfg = self._configs.get(name)
        health = self._health.setdefault(name, _ServerHealth())
        if health.circuit_open_until > time.monotonic():
            return SkillResult(
                ok=False,
                error="MCP server circuit is open",
                artifacts={
                    "error_code": "mcp_circuit_open",
                    "retry_after": round(health.circuit_open_until - time.monotonic(), 3),
                },
            )
        attempts = 1 + (cfg.max_retries if cfg is not None else 0)
        for attempt in range(attempts):
            current = self._tools.get(identity)
            if current is None:
                break
            result = await _invoke_tool(current, args)
            if result.ok or not result.artifacts.get("retryable"):
                health.success()
                return result
            health.failure(str(result.artifacts.get("error_code", "mcp_call_failed")))
            if cfg is None or attempt + 1 >= attempts or self._closing:
                return result
            delay = cfg.reconnect_backoff_seconds * (2**attempt)
            if delay:
                jitter_ceiling = min(delay * 0.25, 0.25)
                jitter = secrets.randbelow(1001) / 1000 * jitter_ceiling
                await asyncio.sleep(delay + jitter)
            try:
                async with self._locks[name]:
                    await self._connect_one(name, cfg)
            except Exception as exc:  # noqa: BLE001
                health.failure(_error_code(exc))
        return SkillResult(
            ok=False,
            error="MCP server remained unavailable after bounded retries",
            artifacts={"error_code": "mcp_retries_exhausted", "retryable": True},
        )

    async def reconnect(self, name: str) -> bool:
        cfg = self._configs.get(name)
        if cfg is None:
            return False
        async with self._locks[name]:
            await self._connect_one(name, cfg)
        return True

    async def read_resource(self, identity: str) -> SkillResult:
        item = self._resources.get(identity)
        if item is None:
            return SkillResult(
                ok=False,
                error="MCP resource is unavailable",
                artifacts={"error_code": "mcp_resource_unavailable"},
            )
        session = self._sessions.get(str(item["server"]))
        if session is None:
            return SkillResult(
                ok=False,
                error="MCP server is unavailable",
                artifacts={"error_code": "mcp_transport_unavailable", "retryable": True},
            )
        try:
            result = await session.read_resource(item["uri"])
        except Exception as exc:  # noqa: BLE001
            self._health[str(item["server"])].failure(_error_code(exc))
            return SkillResult(
                ok=False,
                error="MCP resource read failed",
                artifacts={"error_code": _error_code(exc), "retryable": True},
            )
        parts: list[str] = []
        for content in result.contents:
            text = getattr(content, "text", None)
            if isinstance(text, str):
                parts.append(text)
        self._health[str(item["server"])].success()
        return SkillResult(
            ok=True,
            content="\n".join(parts),
            artifacts={
                "resource_identity": identity,
                "trust_zone": item["trust_zone"],
            },
        )

    async def get_prompt(self, identity: str, arguments: dict[str, str]) -> SkillResult:
        item = self._prompts.get(identity)
        if item is None:
            return SkillResult(
                ok=False,
                error="MCP prompt is unavailable",
                artifacts={"error_code": "mcp_prompt_unavailable"},
            )
        session = self._sessions.get(str(item["server"]))
        if session is None:
            return SkillResult(
                ok=False,
                error="MCP server is unavailable",
                artifacts={"error_code": "mcp_transport_unavailable", "retryable": True},
            )
        try:
            result = await session.get_prompt(item["name"], arguments=arguments)
        except Exception as exc:  # noqa: BLE001
            self._health[str(item["server"])].failure(_error_code(exc))
            return SkillResult(
                ok=False,
                error="MCP prompt retrieval failed",
                artifacts={"error_code": _error_code(exc), "retryable": True},
            )
        parts: list[str] = []
        for message in result.messages:
            text = getattr(message.content, "text", None)
            if isinstance(text, str):
                parts.append(text)
        self._health[str(item["server"])].success()
        return SkillResult(
            ok=True,
            content="\n".join(parts),
            artifacts={"prompt_identity": identity, "trust_zone": item["trust_zone"]},
        )

    async def close_all(self) -> None:
        self._closing = True
        for stack in list(self._stacks.values()):
            await stack.aclose()
        self._stacks.clear()
        self._sessions.clear()
        self._tools.clear()
        self._resources.clear()
        self._prompts.clear()
        for health in self._health.values():
            health.state = "disconnected"

    def get_skills(self) -> dict[str, Skill]:
        adapters = [McpSkillAdapter(tool) for tool in self._tools.values()]
        return {adapter.name: adapter for adapter in adapters}

    def get_tool(self, identity: str) -> _McpTool | None:
        return self._tools.get(identity)

    def catalog(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "tools": self.status(),
            "resources": list(self._resources.values()),
            "prompts": list(self._prompts.values()),
            "quarantined": list(self._quarantined.values()),
        }

    def status(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for tool in self._tools.values():
            health = self._health.get(tool.server_name, _ServerHealth())
            rows.append(
                {
                    "identity": tool.identity,
                    "server": tool.server_name,
                    "tool": tool.tool_name,
                    "description": tool.description,
                    "transport": tool.transport,
                    "auth": tool.auth,
                    "trust_zone": tool.trust_zone.value,
                    "data_ceiling": tool.data_ceiling.value,
                    "risk_level": tool.risk_level,
                    "schema_hash": tool.schema_hash,
                    "schema_status": tool.schema_status,
                    "health": health.state,
                    "consecutive_failures": health.consecutive_failures,
                    "last_error_code": health.last_error_code,
                }
            )
        return rows


def _error_code(exc: BaseException) -> str:
    name = type(exc).__name__
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).casefold()[:64]


_manager: McpManager | None = None


def set_manager(manager: McpManager | None) -> None:
    global _manager
    _manager = manager


def get_manager() -> McpManager | None:
    return _manager
