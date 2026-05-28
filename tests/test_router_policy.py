from __future__ import annotations

import pytest

from yagami.router.schema import Classification, Complexity, Intent, Sensitivity


@pytest.mark.asyncio
async def test_phi_forces_local(make_policy, user_msg):
    policy = make_policy(Classification(sensitivity=Sensitivity.PHI, complexity=Complexity.HIGH))
    decision = await policy.decide(user_msg("anything"))
    assert decision.backend.is_local is True
    assert "phi" in decision.reason


@pytest.mark.asyncio
async def test_phi_medical_picks_medical_lora(make_policy, user_msg):
    from yagami.config import RoutingConfig
    routing = RoutingConfig(lora_variants={"phi_medical": "yagami-phi-medical"})
    policy = make_policy(
        Classification(sensitivity=Sensitivity.PHI_MEDICAL), routing=routing
    )
    decision = await policy.decide(user_msg("patient note ..."))
    assert decision.lora_variant == "yagami-phi-medical"
    assert decision.backend.is_local is True


@pytest.mark.asyncio
async def test_high_complexity_routes_to_claude(make_policy, user_msg):
    policy = make_policy(Classification(complexity=Complexity.HIGH))
    decision = await policy.decide(user_msg("design a distributed consensus protocol"))
    assert decision.backend.name == "anthropic"


@pytest.mark.asyncio
async def test_image_intent_routes_to_stability(make_policy, user_msg):
    policy = make_policy(Classification(intent=Intent.IMAGE))
    decision = await policy.decide(user_msg("draw a red sailboat"))
    assert decision.backend.name == "stability"


@pytest.mark.asyncio
async def test_simple_query_routes_to_local_default(make_policy, user_msg):
    policy = make_policy(Classification(intent=Intent.SIMPLE_QA, complexity=Complexity.LOW))
    decision = await policy.decide(user_msg("what is 2+2"))
    assert decision.backend.name == "ollama"
    assert decision.backend.is_local is True


@pytest.mark.asyncio
async def test_fallback_classifier_without_classifier(make_policy, user_msg):
    policy = make_policy(None)
    decision = await policy.decide(user_msg("draw me a sunset"))
    assert decision.backend.name == "stability"


@pytest.mark.asyncio
async def test_code_intent_attaches_code_lora(make_policy, user_msg):
    from yagami.config import RoutingConfig
    routing = RoutingConfig(lora_variants={"code": "yagami-code"})
    policy = make_policy(Classification(intent=Intent.CODE), routing=routing)
    decision = await policy.decide(user_msg("write a python sort"))
    assert decision.lora_variant == "yagami-code"
    assert decision.backend.name == "ollama"
