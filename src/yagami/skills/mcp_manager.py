"""MCP client: connects to external MCP servers over stdio and exposes their
tools as Yagami Skills - the existing `Skill` protocol, not a new one. This
is what makes every MCP server in the ecosystem usable through Yagami's
existing PHI-aware tool-loop gating for free (see skills/base.py's
`sensitivity_ceiling` mechanism, enforced in router/tool_loop.py).

`McpManager` owns the live subprocess connections for the lifetime of the
running app - started in main.py's lifespan, closed on shutdown. Tool calls
happen throughout that lifetime, not scoped to a single request, so the
stdio_client / ClientSession context managers are held open via an
AsyncExitStack rather than opened-and-closed per call.

No `build()` function here - this module isn't a filesystem-discovered
skill itself (see skills/registry.py); it's the thing that FEEDS discovery
with dynamically-generated skills, one per remote tool. Registration is a
tiny global-singleton hook (set_manager/get_manager), same pattern as
api/config.py's set_policy or api/sessions.py's set_store.
"""

from __future__ import annotations

import logging
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
import httpx

from ..config import McpServerConfig
from ..router.schema import Sensitivity
from .base import Skill, SkillContext, SkillResult
from .mcp_auth import OAuthClientCredentialsAuth, validate_remote_url

log = logging.getLogger("yagami.skills.mcp")

# Per-call ceiling for remote tool execution. Generous enough for slow tools
# (a web search, a big file read), small enough that a wedged server can't
# hold a chat turn hostage.
CALL_TIMEOUT_S = 60.0


@dataclass
class _McpTool:
    server_name: str
    tool_name: str
    description: str
    input_schema: dict
    session: ClientSession
    transport: str = "stdio"
    auth: str = "none"


class McpSkillAdapter:
    """Wraps one remote MCP tool as a Skill. Namespaced `mcp.<server>.<tool>`
    so tools from different servers - or one that happens to share a name
    with a first-party skill - can't collide in the registry."""

    def __init__(self, tool: _McpTool) -> None:
        self._tool = tool
        self.name = f"mcp.{tool.server_name}.{tool.tool_name}"
        self.description = f"[MCP:{tool.server_name}] {tool.description or tool.tool_name}"
        self.input_schema = tool.input_schema or {"type": "object", "properties": {}}
        self.requires_network = True
        # MCP servers are arbitrary, user-configured third-party processes -
        # results flow into whatever backend drives the current tool-use
        # turn (Anthropic-only today; see router/tool_loop.py), same
        # exposure as web.fetch / kb.recall. Most conservative ceiling:
        # refuse whenever the current turn is sensitive at all.
        self.sensitivity_ceiling = Sensitivity.NONE

    async def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        try:
            # Bounded read timeout: an MCP server that hangs (or a tool that
            # never returns) must not hang the chat turn forever - the
            # timeout surfaces as SkillResult(ok=False) like any other
            # failure, and the tool loop carries on.
            result = await self._tool.session.call_tool(
                self._tool.tool_name,
                args,
                read_timeout_seconds=timedelta(seconds=CALL_TIMEOUT_S),
            )
        except Exception as exc:  # noqa: BLE001 - skills must never raise
            return SkillResult(ok=False, error=f"mcp call failed: {exc}")
        text_parts = [
            text
            for block in result.content
            if isinstance((text := getattr(block, "text", None)), str)
        ]
        content = "\n".join(text_parts)
        if result.isError:
            return SkillResult(ok=False, error=content or "MCP tool reported an error")
        return SkillResult(
            ok=True, content=content, artifacts={"mcp_server": self._tool.server_name}
        )


class McpManager:
    """Owns the live connections to every configured MCP server."""

    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self._tools: dict[str, _McpTool] = {}

    async def connect_all(self, servers: dict[str, McpServerConfig]) -> None:
        """Connect to every configured server. A single bad server
        (command not found, crashes on init, ...) is logged and skipped -
        it must not prevent the others, or the app, from starting."""
        for name, server_cfg in servers.items():
            try:
                await self._connect_one(name, server_cfg)
            except Exception as exc:  # noqa: BLE001 - one bad server shouldn't crash boot
                log.warning("mcp server %r failed to connect: %s", name, exc)

    async def _connect_one(self, name: str, server_cfg: McpServerConfig) -> None:
        if server_cfg.transport == "streamable_http":
            await self._connect_http(name, server_cfg)
            return
        params = StdioServerParameters(
            command=server_cfg.command,
            args=server_cfg.args,
            env=server_cfg.env or None,
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        listed = await session.list_tools()
        for tool in listed.tools:
            self._tools[f"{name}.{tool.name}"] = _McpTool(
                server_name=name,
                tool_name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema,
                session=session,
                transport="stdio",
                auth="none",
            )
        log.info("mcp server %r connected: %d tool(s)", name, len(listed.tools))

    async def _connect_http(self, name: str, server_cfg: McpServerConfig) -> None:
        url = validate_remote_url(server_cfg.url, field=f"mcp_servers.{name}.url")
        headers: dict[str, str] = {}
        auth: httpx.Auth | None = None
        oauth_auth: OAuthClientCredentialsAuth | None = None
        if server_cfg.auth == "bearer_env":
            token = os.getenv(server_cfg.bearer_token_env, "")
            if not token:
                raise ValueError(
                    f"MCP server {name!r} token env {server_cfg.bearer_token_env!r} is empty"
                )
            headers["Authorization"] = "Bearer " + token
        elif server_cfg.auth == "client_credentials":
            client_id = os.getenv(server_cfg.oauth_client_id_env, "")
            client_secret = os.getenv(server_cfg.oauth_client_secret_env, "")
            if not client_id or not client_secret:
                raise ValueError(f"MCP server {name!r} OAuth credential env vars are empty")
            oauth_auth = OAuthClientCredentialsAuth(
                token_url=server_cfg.oauth_token_url,
                client_id=client_id,
                client_secret=client_secret,
                scopes=server_cfg.oauth_scopes,
                resource=server_cfg.oauth_resource,
                token_endpoint_auth_method=server_cfg.oauth_token_endpoint_auth_method,
            )
            auth = oauth_auth

        client = await self._stack.enter_async_context(
            httpx.AsyncClient(
                headers=headers,
                auth=auth,
                timeout=httpx.Timeout(60.0, connect=10.0),
                follow_redirects=False,
            )
        )
        if oauth_auth is not None:
            self._stack.push_async_callback(oauth_auth.aclose)
        read, write, _get_session_id = await self._stack.enter_async_context(
            streamable_http_client(url, http_client=client)
        )
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        listed = await session.list_tools()
        for tool in listed.tools:
            self._tools[f"{name}.{tool.name}"] = _McpTool(
                server_name=name,
                tool_name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema,
                session=session,
                transport="streamable_http",
                auth=server_cfg.auth,
            )
        log.info("remote MCP server %r connected: %d tool(s)", name, len(listed.tools))

    async def close_all(self) -> None:
        await self._stack.aclose()
        self._tools.clear()

    def get_skills(self) -> dict[str, Skill]:
        return {
            f"mcp.{t.server_name}.{t.tool_name}": McpSkillAdapter(t) for t in self._tools.values()
        }

    def status(self) -> list[dict]:
        """Per-tool connection status for GET /api/mcp."""
        return [
            {
                "server": t.server_name,
                "tool": t.tool_name,
                "description": t.description,
                "transport": t.transport,
                "auth": t.auth,
            }
            for t in self._tools.values()
        ]


_manager: McpManager | None = None


def set_manager(manager: McpManager | None) -> None:
    global _manager
    _manager = manager


def get_manager() -> McpManager | None:
    return _manager
