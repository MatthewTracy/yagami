from __future__ import annotations

import pytest

from yagami.backends.base import Message
from yagami.router.policy import OverrideRefused, stickier
from yagami.router.schema import Classification, Complexity, Intent, Sensitivity


def test_stickier_picks_most_sensitive():
    assert stickier(None, None) == Sensitivity.NONE
    assert stickier(Sensitivity.NONE, Sensitivity.PHI) == Sensitivity.PHI
    assert stickier(Sensitivity.PHI, Sensitivity.PHI_MEDICAL) == Sensitivity.PHI_MEDICAL
    assert stickier(Sensitivity.PHI_MEDICAL, Sensitivity.SECRET) == Sensitivity.SECRET
    assert stickier(Sensitivity.SECRET, None) == Sensitivity.SECRET
    assert stickier(Sensitivity.PHI_MEDICAL, Sensitivity.PHI) == Sensitivity.PHI_MEDICAL


@pytest.mark.asyncio
async def test_floor_promotes_none_classifier_to_phi(make_policy, classified_user_msg):
    """A long PHI session where turn N classifies as none must NOT be allowed
    to leak to a cloud backend just because complexity is high."""
    policy = make_policy(
        Classification(
            intent=Intent.SIMPLE_QA, sensitivity=Sensitivity.NONE, complexity=Complexity.HIGH
        )
    )
    history = classified_user_msg("summarize what we discussed")
    decision = await policy.decide(history, sensitivity_floor=Sensitivity.PHI_MEDICAL)
    assert decision.backend.is_local is True, (
        f"floor must force local but routed to {decision.backend.name}"
    )
    assert "phi" in decision.reason


@pytest.mark.asyncio
async def test_floor_does_not_demote(make_policy, classified_user_msg):
    """If the classifier sees a stronger sensitivity than the floor, keep it."""
    policy = make_policy(Classification(sensitivity=Sensitivity.SECRET))
    history = classified_user_msg("rotate my key sk-...")
    decision = await policy.decide(history, sensitivity_floor=Sensitivity.PHI)
    assert "secret" in decision.reason
    assert decision.backend.is_local is True


@pytest.mark.asyncio
async def test_floor_blocks_cloud_force_on_phi_session(make_policy, classified_user_msg):
    policy = make_policy(Classification(sensitivity=Sensitivity.NONE))
    history = classified_user_msg("send this to claude")
    with pytest.raises(OverrideRefused):
        await policy.decide(
            history,
            force_backend="anthropic",
            sensitivity_floor=Sensitivity.PHI_MEDICAL,
        )


@pytest.mark.asyncio
async def test_floor_blocks_cloud_slash_override_on_phi_session(make_policy):
    policy = make_policy(None)
    history = [Message(role="user", content="/cloud what did we discuss")]
    with pytest.raises(OverrideRefused):
        await policy.decide(history, sensitivity_floor=Sensitivity.PHI_MEDICAL)


@pytest.mark.asyncio
async def test_no_floor_means_classifier_wins(make_policy, classified_user_msg):
    """Without a floor, an HIGH-complexity non-PHI prompt should escalate."""
    policy = make_policy(
        Classification(
            intent=Intent.COMPLEX_REASONING,
            sensitivity=Sensitivity.NONE,
            complexity=Complexity.HIGH,
        )
    )
    history = classified_user_msg("explain how transformer attention works in detail")
    decision = await policy.decide(history)
    assert decision.backend.name == "anthropic"


@pytest.mark.asyncio
async def test_floor_source_marker_in_classification(make_policy, classified_user_msg):
    policy = make_policy(Classification(sensitivity=Sensitivity.NONE))
    history = classified_user_msg("hi")
    decision = await policy.decide(history, sensitivity_floor=Sensitivity.PHI_MEDICAL)
    assert "floor" in decision.classification["source"], decision.classification
