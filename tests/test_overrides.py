from __future__ import annotations

import pytest

from yagami.backends.base import Message
from yagami.router.overrides import parse
from yagami.router.policy import OverrideRefused


def test_no_command_returns_text_unchanged():
    r = parse("what is 2+2")
    assert r.forced_backend is None
    assert r.stripped_text == "what is 2+2"
    assert r.hint_intent is None
    assert r.hint_complex is False


def test_cloud_command_forces_anthropic():
    r = parse("/cloud explain quantum entanglement")
    assert r.forced_backend == "anthropic"
    assert r.stripped_text == "explain quantum entanglement"


def test_claude_command_forces_anthropic():
    r = parse("/claude write a haiku")
    assert r.forced_backend == "anthropic"


def test_local_command_forces_ollama():
    r = parse("/local what time is it")
    assert r.forced_backend == "ollama"


def test_ollama_command_forces_ollama():
    r = parse("/ollama hi")
    assert r.forced_backend == "ollama"


def test_image_command_forces_stability():
    r = parse("/image a red sailboat")
    assert r.forced_backend == "stability"
    assert r.hint_intent == "image"
    assert r.stripped_text == "a red sailboat"


def test_think_forces_anthropic_with_complex_hint():
    r = parse("/think prove the halting problem")
    assert r.forced_backend == "anthropic"
    assert r.hint_complex is True


def test_code_forces_ollama_with_code_intent():
    r = parse("/code fix this Python")
    assert r.forced_backend == "ollama"
    assert r.hint_intent == "code"


def test_command_is_case_insensitive():
    assert parse("/CLOUD hello").forced_backend == "anthropic"
    assert parse("/Local hi").forced_backend == "ollama"


def test_unknown_command_leaves_text_intact():
    r = parse("/madeup do something")
    assert r.forced_backend is None
    assert r.stripped_text == "/madeup do something"


def test_command_without_arg_strips_to_empty():
    r = parse("/cloud")
    assert r.forced_backend == "anthropic"
    assert r.stripped_text == ""


# ---- Policy-level integration ----


@pytest.mark.asyncio
async def test_cloud_override_routes_to_anthropic(make_policy, classified_user_msg):
    policy = make_policy(None)
    history = [Message(role="user", content="/cloud explain quantum mechanics")]
    decision = await policy.decide(history)
    assert decision.backend.name == "anthropic"
    assert "slash override" in decision.reason


@pytest.mark.asyncio
async def test_cloud_override_refused_on_phi(make_policy):
    policy = make_policy(None)
    history = [Message(
        role="user",
        content="/cloud my SSN is 123-45-6789, help me with identity theft",
    )]
    with pytest.raises(OverrideRefused):
        await policy.decide(history)


@pytest.mark.asyncio
async def test_local_override_routes_to_ollama(make_policy):
    policy = make_policy(None)
    history = [Message(role="user", content="/local fix this")]
    decision = await policy.decide(history)
    assert decision.backend.name == "ollama"
    assert decision.backend.is_local is True


@pytest.mark.asyncio
async def test_force_backend_field_works(make_policy):
    policy = make_policy(None)
    history = [Message(role="user", content="hello")]
    decision = await policy.decide(history, force_backend="anthropic")
    assert decision.backend.name == "anthropic"
    assert "force_backend" in decision.reason


@pytest.mark.asyncio
async def test_force_backend_refused_on_phi(make_policy):
    policy = make_policy(None)
    history = [Message(role="user", content="my SSN is 123-45-6789, help")]
    with pytest.raises(OverrideRefused):
        await policy.decide(history, force_backend="anthropic")


@pytest.mark.asyncio
async def test_force_backend_refused_on_secret(make_policy):
    policy = make_policy(None)
    history = [Message(
        role="user",
        content="rotate my key sk-NsqqVgaZIcLYxcdjvXdR0nHOQyn08RyUMasFjs93i3UfHuvd",
    )]
    with pytest.raises(OverrideRefused):
        await policy.decide(history, force_backend="anthropic")


@pytest.mark.asyncio
async def test_force_backend_unknown_refused(make_policy):
    policy = make_policy(None)
    history = [Message(role="user", content="hello")]
    with pytest.raises(OverrideRefused):
        await policy.decide(history, force_backend="madeup")


@pytest.mark.asyncio
async def test_image_override_routes_to_stability(make_policy):
    policy = make_policy(None)
    history = [Message(role="user", content="/image a boat at sunset")]
    decision = await policy.decide(history)
    assert decision.backend.name == "stability"
    assert decision.effective_user_text == "a boat at sunset"


@pytest.mark.asyncio
async def test_think_override_routes_to_anthropic(make_policy):
    policy = make_policy(None)
    history = [Message(role="user", content="/think hard problem")]
    decision = await policy.decide(history)
    assert decision.backend.name == "anthropic"
