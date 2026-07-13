from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from yagami import config as config_mod
from yagami.api import config as config_api
from yagami.config import ProfileOverrides, RoutingConfig, YagamiConfig, effective_routing
from yagami.main import build_app


# ---- effective_routing() - pure function, no DB/app needed ----


def test_no_active_profile_returns_routing_unchanged():
    cfg = YagamiConfig()
    cfg.routing.default_backend = "anthropic"
    out = effective_routing(cfg)
    assert out.default_backend == "anthropic"
    assert out is not cfg.routing  # copy, not the same object


def test_active_profile_applies_partial_overrides():
    cfg = YagamiConfig()
    cfg.routing.default_backend = "ollama"
    cfg.routing.daily_spend_cap_usd = 5.0
    cfg.routing.long_message_token_threshold = 1500
    cfg.routing.active_profile = "work"
    cfg.profiles["work"] = ProfileOverrides(daily_spend_cap_usd=0)

    out = effective_routing(cfg)
    assert out.daily_spend_cap_usd == 0  # overridden
    assert out.default_backend == "ollama"  # untouched, falls through
    assert out.long_message_token_threshold == 1500  # untouched


def test_unknown_active_profile_falls_back_safely():
    cfg = YagamiConfig()
    cfg.routing.active_profile = "does-not-exist"
    out = effective_routing(cfg)
    assert out.default_backend == cfg.routing.default_backend


def test_profile_can_never_override_phi_must_be_local():
    """ProfileOverrides has no phi_must_be_local field at all - this is
    enforced by the schema, not just by convention. If someone tried to add
    one, this test documents why not to."""
    assert "phi_must_be_local" not in ProfileOverrides.model_fields


# ---- RoutingPolicy.update_config() - live swap ----


@pytest.mark.asyncio
async def test_update_config_changes_default_backend_on_next_decide(make_policy, user_msg):
    policy = make_policy(None)
    before = await policy.decide(user_msg("hi"))
    assert before.backend.name == "ollama"

    policy.update_config(RoutingConfig(default_backend="anthropic"))
    after = await policy.decide(user_msg("hi"))
    assert after.backend.name == "anthropic"


@pytest.mark.asyncio
async def test_update_config_swap_does_not_mutate_old_instance(make_policy, user_msg):
    """update_config() should replace the reference, not mutate an object
    someone else (e.g. a test fixture) might still be holding onto."""
    original = RoutingConfig(default_backend="ollama")
    policy = make_policy(None)
    policy.update_config(original)
    policy.update_config(RoutingConfig(default_backend="anthropic"))
    assert original.default_backend == "ollama"


# ---- API-level: PUT /api/config with profiles ----


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
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
async def test_put_config_creates_and_activates_profile(tmp_config):
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.put(
                "/api/config",
                json={
                    "profiles": {"work": {"daily_spend_cap_usd": 0, "default_backend": "ollama"}},
                    "routing": {"active_profile": "work"},
                },
            )
            assert r.status_code == 200
            data = r.json()
            assert data["config"]["routing"]["active_profile"] == "work"
            assert data["config"]["profiles"]["work"]["daily_spend_cap_usd"] == 0
            # phi_must_be_local is still pinned true - a profile can't touch it.
            assert data["config"]["routing"]["phi_must_be_local"] is True

            # And it actually reached the live policy - no restart needed.
            assert config_api._policy is not None
            assert config_api._policy._config.daily_spend_cap_usd == 0


@pytest.mark.asyncio
async def test_put_config_profile_switch_reflected_on_next_decide(tmp_config, make_policy):
    """End-to-end: PUT a profile active, then confirm RoutingPolicy.decide()
    on THAT SAME policy instance reflects it without a restart."""
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.put(
                "/api/config",
                json={
                    "profiles": {"lean": {"default_backend": "ollama"}},
                    "routing": {"active_profile": "lean", "default_backend": "anthropic"},
                },
            )
            live_policy = config_api._policy
            assert live_policy is not None
            # Fallback classification (no classifier wired in build_app's
            # default) routes trivial prompts to the configured default -
            # confirm it picked up "ollama" from the active profile, not
            # the "anthropic" set alongside it in [routing].
            from yagami.backends.base import Message

            decision = await live_policy.decide([Message(role="user", content="hi")])
            assert decision.backend.name == "ollama"
