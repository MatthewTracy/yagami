from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from yagami import config as config_mod
from yagami.main import build_app


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Point the config module at a throwaway TOML file for each test."""
    cfg_file = tmp_path / "yagami.toml"
    src = Path("config/yagami.toml")
    if src.exists():
        shutil.copy(src, cfg_file)
    monkeypatch.setenv("YAGAMI_CONFIG_PATH", str(cfg_file))
    monkeypatch.setenv("YAGAMI_DB_PATH", str(tmp_path / "yagami.db"))
    config_mod.get_settings.cache_clear()
    config_mod.get_config.cache_clear()
    yield cfg_file
    config_mod.get_settings.cache_clear()
    config_mod.get_config.cache_clear()


@pytest.mark.asyncio
async def test_get_config_returns_current_and_defaults(tmp_config):
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/config")
            assert r.status_code == 200
            data = r.json()
            assert "config" in data
            assert "defaults" in data
            assert "prompts" in data
            assert "phi_medical_default" in data["prompts"]
            assert data["config"]["routing"]["phi_must_be_local"] is True


@pytest.mark.asyncio
async def test_put_config_persists_routing_changes(tmp_config):
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.put(
                "/api/config",
                json={"routing": {"daily_spend_cap_usd": 12.5, "default_backend": "ollama"}},
            )
            assert r.status_code == 200
            data = r.json()
            assert data["ok"] is True
            assert data["config"]["routing"]["daily_spend_cap_usd"] == 12.5

            # Reload and confirm it stuck.
            config_mod.get_config.cache_clear()
            r = await c.get("/api/config")
            assert r.json()["config"]["routing"]["daily_spend_cap_usd"] == 12.5


@pytest.mark.asyncio
async def test_put_config_persists_privacy_retention(tmp_config):
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.put("/api/config", json={"privacy": {"session_retention_days": 30}})
            assert r.status_code == 200
            assert r.json()["config"]["privacy"]["session_retention_days"] == 30

            r = await c.put("/api/config", json={"privacy": {"session_retention_days": -1}})
            assert r.status_code == 422


@pytest.mark.asyncio
async def test_put_config_persists_loopback_foundry_local_settings(tmp_config):
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.put(
                "/api/config",
                json={
                    "foundry_local": {
                        "enabled": True,
                        "base_url": "http://localhost:5272/v1",
                        "model": "test-model-generic-cpu",
                    }
                },
            )
            assert r.status_code == 200
            section = r.json()["config"]["foundry_local"]
            assert section["enabled"] is True
            assert section["model"] == "test-model-generic-cpu"


@pytest.mark.asyncio
async def test_put_config_rejects_remote_foundry_local_endpoint(tmp_config):
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.put(
                "/api/config",
                json={
                    "foundry_local": {
                        "enabled": True,
                        "base_url": "https://foundry.example.com/v1",
                    }
                },
            )
            assert r.status_code == 422


@pytest.mark.asyncio
async def test_put_config_pins_phi_must_be_local_on(tmp_config):
    """Defense in depth: even if the UI somehow PUTs phi_must_be_local=false,
    the server pins it on. Disabling would defeat the local-first guarantee."""
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.put("/api/config", json={"routing": {"phi_must_be_local": False}})
            assert r.status_code == 200
            assert r.json()["config"]["routing"]["phi_must_be_local"] is True


@pytest.mark.asyncio
async def test_put_config_rejects_invalid_types(tmp_config):
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.put(
                "/api/config",
                json={"routing": {"daily_spend_cap_usd": "not-a-number"}},
            )
            assert r.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "routing_patch",
    [
        {"daily_spend_cap_usd": -1},
        {"long_message_token_threshold": 0},
    ],
)
async def test_put_config_rejects_values_that_disable_safety_gates(tmp_config, routing_patch):
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.put("/api/config", json={"routing": routing_patch})
            assert r.status_code == 422


@pytest.mark.asyncio
async def test_toml_round_trip_preserves_fields(tmp_config):
    """Write via the API, read via tomllib, confirm the round-trip values."""
    import tomllib

    app = build_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.put(
                "/api/config",
                json={
                    "anthropic": {"model": "claude-opus-4-8", "max_tokens": 8192},
                    "routing": {"daily_spend_cap_usd": 7.25},
                },
            )
            assert r.status_code == 200

        # Re-read directly from disk via stdlib.
        with tmp_config.open("rb") as f:
            on_disk = tomllib.load(f)
        assert on_disk["anthropic"]["model"] == "claude-opus-4-8"
        assert on_disk["anthropic"]["max_tokens"] == 8192
        assert on_disk["routing"]["daily_spend_cap_usd"] == 7.25
        assert on_disk["routing"]["phi_must_be_local"] is True


def test_toml_round_trip_quotes_dynamic_table_and_inline_keys():
    import tomllib

    from yagami.config import McpServerConfig, ProfileOverrides, YagamiConfig, _serialize_config

    cfg = YagamiConfig(
        profiles={"work.home": ProfileOverrides(daily_spend_cap_usd=2.5)},
        mcp_servers={
            "server one": McpServerConfig(
                command="example",
                env={"API.KEY": "line one\nline two"},
            )
        },
    )

    parsed = tomllib.loads(_serialize_config(cfg))

    assert parsed["profiles"]["work.home"]["daily_spend_cap_usd"] == 2.5
    assert parsed["mcp_servers"]["server one"]["env"]["API.KEY"] == "line one\nline two"
