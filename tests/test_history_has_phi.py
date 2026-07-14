from __future__ import annotations

import pytest

from yagami.backends.base import Message
from yagami.chat.stream import _messages_for_backend
from yagami.router.policy import OverrideRefused
from yagami.router.schema import Classification, Complexity, Intent, Sensitivity


def _conv(prior: str, current: str, *, force_classifier: bool = False) -> list[Message]:
    """Build a 3-turn history. If force_classifier=True, pad the current message
    past the fast-path bypass threshold so the LLM classifier is exercised."""
    if force_classifier:
        current = current + " " + ("x " * 110)
    return [
        Message(role="user", content=prior),
        Message(role="assistant", content="ok"),
        Message(role="user", content=current),
    ]


@pytest.mark.asyncio
async def test_image_route_after_phi_allowed(make_policy):
    """The defining v0.2.10 behavior: PHI in turn 1, image request in turn 3.
    Stability only sees the current prompt - never history - so it's safe."""
    policy = make_policy(Classification(intent=Intent.IMAGE, sensitivity=Sensitivity.NONE))
    history = _conv("Patient Jenny DOB 1962-04-12 has CHF", "draw a red sailboat")
    decision = await policy.decide(history, history_has_phi=True)
    assert decision.backend.name == "stability", (
        f"image gen after PHI must reach stability; got {decision.backend.name}"
    )


@pytest.mark.asyncio
async def test_cloud_text_blocked_when_history_has_phi(make_policy):
    """High complexity with PHI in history: refuse Anthropic, fall to local default."""
    policy = make_policy(
        Classification(
            intent=Intent.COMPLEX_REASONING,
            sensitivity=Sensitivity.NONE,
            complexity=Complexity.HIGH,
        )
    )
    history = _conv(
        "Patient Jane Doe SSN 123-45-6789 has CHF",
        "explain the trade-offs between strong and eventual consistency",
        force_classifier=True,
    )
    decision = await policy.decide(history, history_has_phi=True)
    assert decision.backend.is_local is True, (
        f"cloud text route must be blocked when history has PHI; got {decision.backend.name}"
    )
    assert "history-phi" in decision.classification["source"]


@pytest.mark.asyncio
async def test_clean_history_no_block(make_policy):
    """Without PHI in history, normal routing applies."""
    policy = make_policy(
        Classification(
            intent=Intent.COMPLEX_REASONING,
            sensitivity=Sensitivity.NONE,
            complexity=Complexity.HIGH,
        )
    )
    history = _conv("what's 2+2", "explain transformer attention in detail", force_classifier=True)
    decision = await policy.decide(history, history_has_phi=False)
    assert decision.backend.name == "anthropic"


@pytest.mark.asyncio
async def test_force_cloud_text_refused_on_phi_history(make_policy):
    """force_backend=anthropic refused when history has PHI."""
    policy = make_policy(None)
    history = _conv("Pt has stage IV NSCLC, MRN 12345", "what is 2+2")
    with pytest.raises(OverrideRefused) as exc:
        await policy.decide(history, force_backend="anthropic", history_has_phi=True)
    assert "history" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_force_cloud_image_allowed_on_phi_history(make_policy):
    """force_backend=stability OK even with PHI history (no history sent)."""
    policy = make_policy(None)
    history = _conv("Pt has stage IV NSCLC, MRN 12345", "a sailboat")
    decision = await policy.decide(history, force_backend="stability", history_has_phi=True)
    assert decision.backend.name == "stability"


@pytest.mark.asyncio
async def test_slash_cloud_refused_on_phi_history(make_policy):
    """/cloud refused when history has PHI."""
    policy = make_policy(None)
    history = [
        Message(role="user", content="Patient Jenny has BP 158/94, BNP 612"),
        Message(role="assistant", content="ok"),
        Message(role="user", content="/cloud what did we discuss"),
    ]
    with pytest.raises(OverrideRefused) as exc:
        await policy.decide(history, history_has_phi=True)
    assert "history" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_slash_image_allowed_on_phi_history(make_policy):
    """/image works even after PHI - only the prompt is sent to Stability."""
    policy = make_policy(None)
    history = [
        Message(role="user", content="Patient Jenny has BP 158/94, BNP 612"),
        Message(role="assistant", content="ok"),
        Message(role="user", content="/image a red sailboat"),
    ]
    decision = await policy.decide(history, history_has_phi=True)
    assert decision.backend.name == "stability"


@pytest.mark.asyncio
async def test_per_turn_classification_no_floor(make_policy):
    """A non-PHI turn after a PHI turn is classified on its OWN merits.
    Without history_has_phi=True (e.g. caller decides differently), it can
    route freely. This is the per-turn fix the user asked for."""
    policy = make_policy(Classification(intent=Intent.IMAGE, sensitivity=Sensitivity.NONE))
    history = _conv("Patient Jenny has CHF", "draw a sailboat")
    # Caller passes history_has_phi=False to simulate the "let me decide
    # per-turn" stance - image gen is always allowed regardless.
    decision = await policy.decide(history, history_has_phi=False)
    assert decision.backend.name == "stability"


def test_reset_context_never_sends_prior_phi_to_backend():
    """Disabling the routing gate is safe only if the old context is omitted."""
    history = [
        Message(role="user", content="Patient Jenny DOB 1962-04-12 has CHF"),
        Message(role="assistant", content="noted"),
        Message(role="user", content="what is 2+2"),
    ]

    visible = _messages_for_backend(history, reset_context=True)

    assert visible == [history[-1]]
    assert all("Jenny" not in message.content for message in visible)


def test_normal_context_still_includes_history():
    history = [
        Message(role="user", content="hello"),
        Message(role="assistant", content="hi"),
        Message(role="user", content="continue"),
    ]
    assert _messages_for_backend(history) == history
