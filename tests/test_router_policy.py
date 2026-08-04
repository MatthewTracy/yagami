from __future__ import annotations

import pytest

from yagami.router.schema import Classification, Complexity, Intent, Sensitivity


@pytest.mark.asyncio
async def test_phi_forces_local(make_policy, classified_user_msg):
    policy = make_policy(Classification(sensitivity=Sensitivity.PHI, complexity=Complexity.HIGH))
    decision = await policy.decide(classified_user_msg("anything"))
    assert decision.backend.is_local is True
    assert "phi" in decision.reason


@pytest.mark.asyncio
async def test_secret_forces_local(make_policy, classified_user_msg):
    policy = make_policy(Classification(sensitivity=Sensitivity.SECRET, complexity=Complexity.HIGH))
    decision = await policy.decide(classified_user_msg("anything"))
    assert decision.backend.is_local is True
    assert "secret" in decision.reason


@pytest.mark.asyncio
async def test_phi_medical_attaches_consumer_health_prompt(make_policy, classified_user_msg):
    from yagami.router.prompts import PHI_MEDICAL_SYSTEM_PROMPT

    policy = make_policy(Classification(sensitivity=Sensitivity.PHI_MEDICAL))
    decision = await policy.decide(classified_user_msg("patient note ..."))
    assert decision.backend.is_local is True
    assert decision.system_prompt == PHI_MEDICAL_SYSTEM_PROMPT
    assert "Do not assume the user is a clinician" in decision.system_prompt
    assert "Never infer that a diagnosis was confirmed" in decision.system_prompt


@pytest.mark.asyncio
async def test_obvious_personal_health_does_not_call_model_classifier(backends, user_msg):
    from yagami.config import RoutingConfig
    from yagami.router.policy import RoutingPolicy

    async def classifier_must_not_run(_text: str):
        raise AssertionError("semantic classifier should not run for obvious personal health")

    policy = RoutingPolicy(
        config=RoutingConfig(),
        backends=backends,
        classifier=classifier_must_not_run,
    )
    decision = await policy.decide(
        user_msg("I just went to get seen for my flu can you review my info from them")
    )

    assert decision.backend.is_local
    assert decision.classification["sensitivity"] == "phi_medical"
    assert decision.classification["source"] == "rules-fast-path"


@pytest.mark.asyncio
async def test_explicit_clinician_purpose_uses_clinician_prompt(make_policy, classified_user_msg):
    from yagami.router.prompts import PHI_MEDICAL_CLINICIAN_SYSTEM_PROMPT

    policy = make_policy(Classification(sensitivity=Sensitivity.PHI_MEDICAL))
    decision = await policy.decide(
        classified_user_msg("patient note ..."),
        purpose="clinical-documentation",
    )
    assert decision.system_prompt == PHI_MEDICAL_CLINICIAN_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_medical_sensitivity_is_sticky_for_text_follow_up(make_policy, user_msg):
    policy = make_policy(None)
    decision = await policy.decide(
        user_msg("What should I do next?"),
        history_sensitivity=Sensitivity.PHI_MEDICAL,
    )
    assert decision.classification["sensitivity"] == "phi_medical"
    assert decision.classification["data_labels"] == ["phi"]
    assert "history-phi" in decision.classification["source"]
    assert decision.backend.is_local


@pytest.mark.asyncio
async def test_nonmedical_phi_attaches_private_data_system_prompt(make_policy, classified_user_msg):
    from yagami.router.prompts import PHI_SYSTEM_PROMPT

    policy = make_policy(Classification(intent=Intent.SIMPLE_QA, sensitivity=Sensitivity.PHI))
    decision = await policy.decide(classified_user_msg("summarize delivery address"))
    assert decision.backend.is_local
    assert decision.system_prompt == PHI_SYSTEM_PROMPT
    assert decision.model_override == "phi4-mini"
    assert decision.lora_variant is None


@pytest.mark.asyncio
async def test_high_complexity_routes_to_claude(make_policy, classified_user_msg):
    policy = make_policy(Classification(complexity=Complexity.HIGH))
    decision = await policy.decide(classified_user_msg("design a distributed consensus protocol"))
    assert decision.backend.name == "anthropic"


@pytest.mark.asyncio
async def test_image_intent_routes_to_stability(make_policy, classified_user_msg):
    policy = make_policy(Classification(intent=Intent.IMAGE))
    decision = await policy.decide(classified_user_msg("draw a red sailboat"))
    assert decision.backend.name == "stability"


@pytest.mark.asyncio
async def test_simple_query_routes_to_local_default(make_policy, classified_user_msg):
    policy = make_policy(Classification(intent=Intent.SIMPLE_QA, complexity=Complexity.LOW))
    decision = await policy.decide(classified_user_msg("what is 2+2"))
    assert decision.backend.name == "ollama"
    assert decision.backend.is_local is True


@pytest.mark.asyncio
async def test_fallback_classifier_without_classifier(make_policy, classified_user_msg):
    policy = make_policy(None)
    decision = await policy.decide(classified_user_msg("draw me a sunset"))
    assert decision.backend.name == "stability"


@pytest.mark.asyncio
async def test_code_intent_attaches_code_lora(make_policy, classified_user_msg):
    from yagami.config import RoutingConfig

    routing = RoutingConfig(lora_variants={"code": "yagami-code"})
    policy = make_policy(Classification(intent=Intent.CODE), routing=routing)
    decision = await policy.decide(classified_user_msg("write a python sort"))
    assert decision.lora_variant == "yagami-code"
    assert decision.backend.name == "ollama"
