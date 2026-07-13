from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mcp.types import CallToolResult, TextContent

from yagami.config import McpServerConfig
from yagami.router.schema import Sensitivity
from yagami.skills.base import SkillContext
from yagami.skills.mcp_manager import (
    McpManager,
    McpSkillAdapter,
    _McpTool,
    get_manager,
    set_manager,
)

_FIXTURE_SERVER = Path(__file__).parent / "fixtures" / "echo_mcp_server.py"


def _ctx(sens: Sensitivity = Sensitivity.NONE) -> SkillContext:
    return SkillContext(session_id="s1", session_sensitivity=sens)


# ---- McpSkillAdapter, mocked session (no subprocess) ----


class _FakeSession:
    def __init__(self, result: CallToolResult | None = None, raise_exc: Exception | None = None):
        self._result = result
        self._raise_exc = raise_exc
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict) -> CallToolResult:
        self.calls.append((name, arguments))
        if self._raise_exc is not None:
            raise self._raise_exc
        assert self._result is not None
        return self._result


def _tool(session, name="echo", description="Echo text back") -> _McpTool:
    return _McpTool(
        server_name="testserver",
        tool_name=name,
        description=description,
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        session=session,
    )


def test_adapter_name_is_namespaced():
    adapter = McpSkillAdapter(_tool(_FakeSession()))
    assert adapter.name == "mcp.testserver.echo"
    assert "testserver" in adapter.description


def test_adapter_sensitivity_ceiling_is_conservative():
    """Same floor as web.fetch / kb.recall - results can reach a cloud
    backend via the tool loop, so refuse whenever the turn is sensitive."""
    adapter = McpSkillAdapter(_tool(_FakeSession()))
    assert adapter.sensitivity_ceiling == Sensitivity.NONE


@pytest.mark.asyncio
async def test_adapter_run_returns_text_content():
    result = CallToolResult(content=[TextContent(type="text", text="HELLO")], isError=False)
    session = _FakeSession(result=result)
    adapter = McpSkillAdapter(_tool(session))

    res = await adapter.run({"text": "hello"}, _ctx())
    assert res.ok is True
    assert res.content == "HELLO"
    assert res.artifacts["mcp_server"] == "testserver"
    assert session.calls == [("echo", {"text": "hello"})]


@pytest.mark.asyncio
async def test_adapter_run_surfaces_tool_reported_error():
    result = CallToolResult(
        content=[TextContent(type="text", text="boom")],
        isError=True,
    )
    session = _FakeSession(result=result)
    adapter = McpSkillAdapter(_tool(session))

    res = await adapter.run({}, _ctx())
    assert res.ok is False
    assert res.error == "boom"


@pytest.mark.asyncio
async def test_adapter_run_never_raises_on_transport_failure():
    session = _FakeSession(raise_exc=ConnectionError("subprocess died"))
    adapter = McpSkillAdapter(_tool(session))

    res = await adapter.run({}, _ctx())
    assert res.ok is False
    assert "subprocess died" in res.error


# ---- McpManager against a real subprocess MCP server ----


@pytest.mark.asyncio
async def test_manager_connects_and_lists_real_tools():
    manager = McpManager()
    try:
        await manager.connect_all(
            {"echo": McpServerConfig(command=sys.executable, args=[str(_FIXTURE_SERVER)])}
        )
        skills = manager.get_skills()
        assert "mcp.echo.echo" in skills
        assert "mcp.echo.fail" in skills
        assert skills["mcp.echo.echo"].input_schema["type"] == "object"

        status = manager.status()
        assert any(s["server"] == "echo" and s["tool"] == "echo" for s in status)
    finally:
        await manager.close_all()


@pytest.mark.asyncio
async def test_manager_round_trips_a_real_tool_call():
    """The actual point of MCP client support: call a tool in a real
    subprocess speaking the real MCP protocol, not a mock."""
    manager = McpManager()
    try:
        await manager.connect_all(
            {"echo": McpServerConfig(command=sys.executable, args=[str(_FIXTURE_SERVER)])}
        )
        skills = manager.get_skills()
        result = await skills["mcp.echo.echo"].run({"text": "hello from yagami"}, _ctx())
        assert result.ok is True
        assert result.content == "HELLO FROM YAGAMI"
    finally:
        await manager.close_all()


@pytest.mark.asyncio
async def test_manager_surfaces_real_tool_error():
    manager = McpManager()
    try:
        await manager.connect_all(
            {"echo": McpServerConfig(command=sys.executable, args=[str(_FIXTURE_SERVER)])}
        )
        skills = manager.get_skills()
        result = await skills["mcp.echo.fail"].run({}, _ctx())
        assert result.ok is False
        assert result.error
    finally:
        await manager.close_all()


@pytest.mark.asyncio
async def test_manager_bad_server_config_does_not_crash_others():
    """One misconfigured server (command not found) must not prevent a
    working one from connecting - see connect_all()'s per-server try/except."""
    manager = McpManager()
    try:
        await manager.connect_all(
            {
                "broken": McpServerConfig(command="this-command-does-not-exist-anywhere"),
                "echo": McpServerConfig(command=sys.executable, args=[str(_FIXTURE_SERVER)]),
            }
        )
        skills = manager.get_skills()
        assert "mcp.echo.echo" in skills
        assert not any(k.startswith("mcp.broken.") for k in skills)
    finally:
        await manager.close_all()


# ---- registry integration ----


def test_registry_merges_in_connected_mcp_skills():
    from yagami.skills.registry import discover_skills

    fake_manager = McpManager()
    fake_manager._tools["testserver.echo"] = _tool(_FakeSession())
    set_manager(fake_manager)
    try:
        skills = discover_skills()
        assert "mcp.testserver.echo" in skills
        assert "calc.eval" in skills  # filesystem-discovered skills still present too
    finally:
        set_manager(None)


def test_registry_has_no_mcp_skills_when_manager_unset():
    from yagami.skills.registry import discover_skills

    assert get_manager() is None
    skills = discover_skills()
    assert not any(k.startswith("mcp.") for k in skills)
    assert "mcp_manager" not in skills  # helper module, not a skill


# ---- config: mcp_servers TOML round-trip ----


def test_mcp_servers_config_round_trips_through_toml():
    """mcp_servers is a dict[str, McpServerConfig] where each value has its
    own nested `env` dict - a shape the hand-rolled TOML writer
    (config._serialize_config) hadn't been exercised with before. A server
    with no args/env at all must round-trip too, not just the happy path."""
    import tomllib

    from yagami.config import YagamiConfig, _serialize_config

    cfg = YagamiConfig()
    cfg.mcp_servers["echo"] = McpServerConfig(
        command="python", args=["-m", "echo_server"], env={"FOO": "bar"}
    )
    cfg.mcp_servers["bare"] = McpServerConfig(command="npx")

    parsed = tomllib.loads(_serialize_config(cfg))
    back = YagamiConfig.model_validate(parsed)

    assert back.mcp_servers["echo"].command == "python"
    assert back.mcp_servers["echo"].args == ["-m", "echo_server"]
    assert back.mcp_servers["echo"].env == {"FOO": "bar"}
    assert back.mcp_servers["bare"].args == []
    assert back.mcp_servers["bare"].env == {}


# ---- GET /api/mcp ----


@pytest.mark.asyncio
async def test_mcp_status_endpoint_reflects_manager_state():
    from httpx import ASGITransport, AsyncClient

    from yagami.main import build_app

    fake_manager = McpManager()
    fake_manager._tools["testserver.echo"] = _tool(_FakeSession())
    set_manager(fake_manager)
    try:
        app = build_app()
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                r = await c.get("/api/mcp")
                assert r.status_code == 200
                data = r.json()
                assert data["connected"] is True
                assert data["count"] == 1
                assert data["tools"][0]["server"] == "testserver"
    finally:
        set_manager(None)


@pytest.mark.asyncio
async def test_mcp_status_endpoint_when_nothing_configured():
    from httpx import ASGITransport, AsyncClient

    from yagami.main import build_app

    assert get_manager() is None
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/mcp")
            assert r.status_code == 200
            assert r.json() == {"connected": False, "tools": [], "count": 0}
