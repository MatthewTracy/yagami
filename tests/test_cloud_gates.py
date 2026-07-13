"""The gates must hold for EVERY cloud backend, not a hardcoded name list.

Regression suite for a real bug shipped in v0.3.0: the history-PHI gate
checked `backend.name == "anthropic"` and the spend gate checked
`name in ("anthropic", "stability")`, so the newly added cloud backends
(mistral, groq, openrouter, gemini, openai) silently bypassed both. These
tests parametrize over cloud-text backends so adding the next backend can't
reopen the hole.
"""

from __future__ import annotations

import pytest

from yagami.backends.base import Capability, Message
from yagami.config import ProfileOverrides, RoutingConfig, YagamiConfig, effective_routing
from yagami.router.policy import OverrideRefused, RoutingPolicy

from .conftest import FakeBackend

CLOUD_TEXT = ["anthropic", "mistral", "groq"]  # every cloud-text fake in conftest


# ---- history-PHI gate: slash override + force_backend ----


@pytest.mark.asyncio
@pytest.mark.parametrize("cloud", CLOUD_TEXT)
async def test_force_backend_refused_on_phi_history_for_every_cloud(make_policy, cloud):
    policy = make_policy(None)
    history = [Message(role="user", content="hello")]
    with pytest.raises(OverrideRefused):
        await policy.decide(history, force_backend=cloud, history_has_phi=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("cloud", CLOUD_TEXT)
async def test_slash_override_refused_on_phi_history_for_every_cloud(make_policy, cloud):
    policy = make_policy(None)
    history = [Message(role="user", content=f"/{cloud} summarize our discussion")]
    with pytest.raises(OverrideRefused):
        await policy.decide(history, history_has_phi=True)


@pytest.mark.asyncio
async def test_stability_still_allowed_with_phi_history(make_policy):
    """Image gen only sends the current prompt - the history gate must keep
    NOT applying to it (capability-based check, IMAGE not TEXT)."""
    policy = make_policy(None)
    history = [Message(role="user", content="/image a red sailboat")]
    decision = await policy.decide(history, history_has_phi=True)
    assert decision.backend.name == "stability"


# ---- spend gate: slash override + force_backend ----


@pytest.mark.asyncio
@pytest.mark.parametrize("cloud", CLOUD_TEXT + ["stability"])
async def test_force_backend_refused_when_spend_blocked_for_every_cloud(make_policy, cloud):
    policy = make_policy(None)
    history = [Message(role="user", content="hello")]
    with pytest.raises(OverrideRefused):
        await policy.decide(history, force_backend=cloud, spend_blocked=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("cloud", CLOUD_TEXT)
async def test_slash_override_refused_when_spend_blocked_for_every_cloud(make_policy, cloud):
    policy = make_policy(None)
    history = [Message(role="user", content=f"/{cloud} hello")]
    with pytest.raises(OverrideRefused):
        await policy.decide(history, spend_blocked=True)


@pytest.mark.asyncio
async def test_local_override_fine_when_spend_blocked(make_policy):
    policy = make_policy(None)
    history = [Message(role="user", content="/local hello")]
    decision = await policy.decide(history, spend_blocked=True)
    assert decision.backend.is_local is True


# ---- default-route gate: cloud default_backend must not bypass ----


@pytest.mark.asyncio
@pytest.mark.parametrize("cloud", CLOUD_TEXT)
async def test_cloud_default_falls_back_to_local_on_phi_history(make_policy, cloud):
    policy = make_policy(None, routing=RoutingConfig(default_backend=cloud))
    history = [Message(role="user", content="hello")]
    decision = await policy.decide(history, history_has_phi=True)
    assert decision.backend.is_local is True
    assert "history-phi" in decision.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("cloud", CLOUD_TEXT)
async def test_cloud_default_falls_back_to_local_when_spend_blocked(make_policy, cloud):
    """Also covers the fast-path: short prompts bypass the classifier and
    previously never even saw spend_blocked."""
    policy = make_policy(None, routing=RoutingConfig(default_backend=cloud))
    history = [Message(role="user", content="hello")]
    decision = await policy.decide(history, spend_blocked=True)
    assert decision.backend.is_local is True
    assert "spend-cap" in decision.reason


@pytest.mark.asyncio
async def test_local_default_unaffected_by_gates(make_policy):
    policy = make_policy(None)
    history = [Message(role="user", content="hello")]
    decision = await policy.decide(history, history_has_phi=True, spend_blocked=True)
    assert decision.backend.name == "ollama"
    assert "fallback" not in decision.reason  # was already local; no downgrade happened


# ---- block_cloud config plumbing ----


def test_block_cloud_default_off():
    assert YagamiConfig().routing.block_cloud is False


def test_profile_can_turn_block_cloud_on():
    cfg = YagamiConfig()
    cfg.routing.active_profile = "work"
    cfg.profiles["work"] = ProfileOverrides(block_cloud=True)
    assert effective_routing(cfg).block_cloud is True


def test_profile_can_turn_block_cloud_off_again():
    cfg = YagamiConfig()
    cfg.routing.block_cloud = True
    cfg.routing.active_profile = "personal"
    cfg.profiles["personal"] = ProfileOverrides(block_cloud=False)
    assert effective_routing(cfg).block_cloud is False


def test_profile_without_block_cloud_inherits_base():
    cfg = YagamiConfig()
    cfg.routing.block_cloud = True
    cfg.routing.active_profile = "p"
    cfg.profiles["p"] = ProfileOverrides(daily_spend_cap_usd=1.0)
    assert effective_routing(cfg).block_cloud is True


# ---- vision backend selection ----


def _policy_with(backends: dict) -> RoutingPolicy:
    return RoutingPolicy(config=RoutingConfig(), backends=backends, classifier=None)


def test_first_vision_backend_prefers_anthropic():
    policy = _policy_with(
        {
            "anthropic": FakeBackend(
                "anthropic", is_local=False, capabilities={Capability.TEXT, Capability.VISION}
            ),
            "gemini": FakeBackend(
                "gemini", is_local=False, capabilities={Capability.TEXT, Capability.VISION}
            ),
        }
    )
    assert policy.first_vision_backend() == "anthropic"


def test_first_vision_backend_falls_back_to_gemini():
    policy = _policy_with(
        {
            "ollama": FakeBackend("ollama", is_local=True),
            "gemini": FakeBackend(
                "gemini", is_local=False, capabilities={Capability.TEXT, Capability.VISION}
            ),
        }
    )
    assert policy.first_vision_backend() == "gemini"


def test_first_vision_backend_none_when_no_vision():
    policy = _policy_with({"ollama": FakeBackend("ollama", is_local=True)})
    assert policy.first_vision_backend() is None


def test_first_vision_backend_skips_text_only_anthropic():
    """Capability check, not name check: an anthropic entry without VISION
    declared must not be picked."""
    policy = _policy_with(
        {
            "anthropic": FakeBackend("anthropic", is_local=False, capabilities={Capability.TEXT}),
            "openai": FakeBackend(
                "openai", is_local=False, capabilities={Capability.TEXT, Capability.VISION}
            ),
        }
    )
    assert policy.first_vision_backend() == "openai"
