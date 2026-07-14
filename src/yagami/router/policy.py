from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from ..backends.base import Backend, Capability, Message
from ..config import RoutingConfig
from .fast_path import can_bypass
from .overrides import OverrideResult, parse as parse_override
from .prompts import PHI_MEDICAL_SYSTEM_PROMPT, PHI_SYSTEM_PROMPT
from .schema import Classification, Complexity, Intent, Sensitivity

Classifier = Callable[[str], Awaitable[Classification]]


class SensitivityInspector(Protocol):
    async def inspect(self, text: str) -> Sensitivity: ...


# Order used when we need to compare two sensitivity labels.
_SENSITIVITY_ORDER: dict[Sensitivity, int] = {
    Sensitivity.NONE: 0,
    Sensitivity.PHI: 1,
    Sensitivity.PHI_MEDICAL: 2,
    Sensitivity.SECRET: 3,
}


def stickier(a: Sensitivity | None, b: Sensitivity | None) -> Sensitivity:
    """Return whichever of a / b is more sensitive. Treats None as NONE."""
    a = a or Sensitivity.NONE
    b = b or Sensitivity.NONE
    return a if _SENSITIVITY_ORDER[a] >= _SENSITIVITY_ORDER[b] else b


class OverrideRefused(Exception):
    """User asked for a backend that the policy refuses (e.g. /cloud on PHI)."""


def _is_cloud_text(backend: Backend) -> bool:
    """True for backends that are BOTH cloud-hosted AND receive the chat
    history (TEXT capability). This is the set the history-PHI gate applies
    to. Image gen (stability) is cloud but only ever sees the current
    prompt, so it's deliberately excluded - same behavior the gate has
    always had, just no longer expressed as `name == "anthropic"`, which
    silently exempted every OTHER cloud text backend (openai, mistral,
    groq, openrouter, gemini)."""
    return not backend.is_local and Capability.TEXT in backend.capabilities


def _privacy_system_prompt(sensitivity: Sensitivity | None) -> str | None:
    if sensitivity == Sensitivity.PHI_MEDICAL:
        return PHI_MEDICAL_SYSTEM_PROMPT
    if sensitivity == Sensitivity.PHI:
        return PHI_SYSTEM_PROMPT
    return None


@dataclass
class RoutingDecision:
    backend: Backend
    reason: str
    classification: dict
    lora_variant: str | None = None
    model_override: str | None = None
    system_prompt: str | None = None
    effective_user_text: str | None = None  # set when override stripped a prefix
    use_tools: bool = False  # v0.2.14: stream branches into tool_loop when True


class RoutingPolicy:
    def __init__(
        self,
        *,
        config: RoutingConfig,
        backends: dict[str, Backend],
        classifier: Classifier | None = None,
        sensitivity_inspector: SensitivityInspector | None = None,
    ) -> None:
        self._config = config
        self._backends = backends
        self._classifier = classifier
        self._sensitivity_inspector = sensitivity_inspector

    @property
    def config(self) -> RoutingConfig:
        return self._config

    def update_config(self, config: RoutingConfig) -> None:
        """Swap in a new effective RoutingConfig - e.g. after `PUT
        /api/config` or a profile switch (see config.effective_routing).
        `decide()` reads `self._config` fresh on every call, so this takes
        effect on the next turn with no restart. A decide() already in
        flight keeps using whatever it already read - plain attribute
        assignment, no lock needed."""
        self._config = config

    async def decide(
        self,
        history: list[Message],
        *,
        force_backend: str | None = None,
        spend_blocked: bool = False,
        history_has_phi: bool = False,
    ) -> RoutingDecision:
        """Route the current turn.

        `history_has_phi` is the caller's read of whether any earlier message in
        the conversation contains PHI/secret content. The current turn is
        classified independently - no sticky floor - but cloud TEXT backends
        (all of them, not just anthropic - see _is_cloud_text) are refused
        when history_has_phi, because the whole history is sent in those
        requests. Image gen (stability) ignores history and is always safe
        to route to.

        `spend_blocked` gates EVERY cloud backend by is_local, regardless of
        name - it's set by the caller when the daily cap is exceeded or the
        active profile has block_cloud on.
        """
        last_text = next((m.content for m in reversed(history) if m.role == "user"), "")
        external_sensitivity = (
            await self._sensitivity_inspector.inspect(last_text)
            if self._sensitivity_inspector is not None
            else Sensitivity.NONE
        )
        if self._sensitivity_inspector is not None:
            last_user_index = max(
                (index for index, message in enumerate(history) if message.role == "user"),
                default=0,
            )
            prior_text = "\n".join(message.content for message in history[:last_user_index])
            if prior_text:
                history_has_phi = history_has_phi or (
                    await self._sensitivity_inspector.inspect(prior_text) != Sensitivity.NONE
                )

        # 1. Slash-command override at the start of the message.
        override = parse_override(last_text, self._backends.keys())
        if override.forced_backend:
            target = self._backends.get(override.forced_backend)
            if spend_blocked and target is not None and not target.is_local:
                raise OverrideRefused(
                    "override refused: cloud routes blocked (daily spend cap reached or "
                    f"block_cloud active); {override.forced_backend!r} is cloud"
                )
            classified_sensitivity = await self._cloud_override_sensitivity(
                override.stripped_text, target
            )
            classified_sensitivity = stickier(classified_sensitivity, external_sensitivity)
            return self._apply_override(
                override,
                last_text,
                history_has_phi=history_has_phi,
                classified_sensitivity=classified_sensitivity,
            )

        # 2. Programmatic force_backend (WS field).
        if force_backend:
            target = self._backends.get(force_backend)
            if spend_blocked and target is not None and not target.is_local:
                raise OverrideRefused(
                    "force_backend refused: cloud routes blocked (daily spend cap reached "
                    f"or block_cloud active); {force_backend!r} is cloud"
                )
            classified_sensitivity = await self._cloud_override_sensitivity(last_text, target)
            classified_sensitivity = stickier(classified_sensitivity, external_sensitivity)
            return self._apply_force_backend(
                force_backend,
                last_text,
                history_has_phi=history_has_phi,
                classified_sensitivity=classified_sensitivity,
            )

        # 3. Rule-based fast-path bypass for high-confidence cases.
        bypass = can_bypass(last_text)
        if bypass is not None:
            bypass.sensitivity = stickier(bypass.sensitivity, external_sensitivity)
            return self._apply_rules(
                bypass,
                "rules-fast-path+external"
                if external_sensitivity != Sensitivity.NONE
                else "rules-fast-path",
                last_text,
                spend_blocked=spend_blocked,
                history_has_phi=history_has_phi,
            )

        # 4. LLM classifier.
        if self._classifier is None:
            classification = self._fallback_classify(last_text)
            source = "fallback"
        else:
            try:
                classification = await self._classifier(last_text)
                source = "classifier"
            except Exception:
                classification = self._fallback_classify(last_text)
                if self._config.fail_closed_on_classifier_error:
                    cls_dict = classification.model_dump(mode="json")
                    cls_dict["source"] = "fallback-after-error-local"
                    backend = self._preferred_local()
                    return RoutingDecision(
                        backend=backend,
                        reason=(
                            "privacy classifier unavailable; failed closed to local "
                            f"({backend.name})"
                        ),
                        classification=cls_dict,
                    )
                source = "fallback-after-error"

        classification.sensitivity = stickier(classification.sensitivity, external_sensitivity)
        if external_sensitivity != Sensitivity.NONE:
            source += "+external"

        return self._apply_rules(
            classification,
            source,
            last_text,
            spend_blocked=spend_blocked,
            history_has_phi=history_has_phi,
        )

    def _apply_override(
        self,
        override: OverrideResult,
        original_text: str,
        *,
        history_has_phi: bool = False,
        classified_sensitivity: Sensitivity | None = None,
    ) -> RoutingDecision:
        from .fast_path import _has_phi, _has_secret  # local import to avoid cycle

        sensitive: Sensitivity | None = classified_sensitivity
        if _has_phi(override.stripped_text):
            sensitive = Sensitivity.PHI
        elif _has_secret(override.stripped_text):
            sensitive = Sensitivity.SECRET

        if sensitive not in {None, Sensitivity.NONE} and self._config.phi_must_be_local:
            target = self._backends.get(override.forced_backend or "")
            if target is not None and not target.is_local:
                raise OverrideRefused(
                    f"override ignored: sensitivity={sensitive.value} requires local backend, "
                    f"requested {override.forced_backend!r} is cloud"
                )

        backend = self._backends.get(override.forced_backend or "")
        if backend is None:
            raise OverrideRefused(f"override backend {override.forced_backend!r} not available")

        # Cloud TEXT backends see history. If history has PHI, refuse explicitly
        # - the user's current prompt may be safe but we'd ship Jenny's note to
        # the cloud alongside it. Applies to EVERY cloud text backend, not a
        # named one. Image backends only see the current prompt, so they're
        # safe to route to.
        if _is_cloud_text(backend) and history_has_phi:
            raise OverrideRefused(
                f"override refused: chat history contains PHI; cloud text backend "
                f"{backend.name!r} would include it in context. Start a new chat or use /local."
            )

        intent = (
            Intent(override.hint_intent)
            if override.hint_intent
            else (Intent.COMPLEX_REASONING if override.hint_complex else Intent.SIMPLE_QA)
        )
        complexity = Complexity.HIGH if override.hint_complex else Complexity.LOW
        cls = Classification(
            intent=intent,
            sensitivity=sensitive or Sensitivity.NONE,
            complexity=complexity,
        )
        cls_dict = cls.model_dump(mode="json")
        cls_dict["source"] = "slash-override"
        sysprompt = _privacy_system_prompt(sensitive)
        return RoutingDecision(
            backend=backend,
            reason=f"slash override → {backend.name}",
            classification=cls_dict,
            system_prompt=sysprompt,
            model_override=self._config.local_model_overrides.get(sensitive.value)
            if sensitive not in {None, Sensitivity.NONE}
            else None,
            effective_user_text=override.stripped_text or None,
        )

    def _apply_force_backend(
        self,
        name: str,
        last_text: str,
        *,
        history_has_phi: bool = False,
        classified_sensitivity: Sensitivity | None = None,
    ) -> RoutingDecision:
        from .fast_path import _has_phi, _has_secret  # local import to avoid cycle

        sensitive: Sensitivity | None = classified_sensitivity
        if _has_phi(last_text):
            sensitive = Sensitivity.PHI
        elif _has_secret(last_text):
            sensitive = Sensitivity.SECRET

        backend = self._backends.get(name)
        if backend is None:
            raise OverrideRefused(f"force_backend {name!r} not registered")
        if (
            sensitive not in {None, Sensitivity.NONE}
            and self._config.phi_must_be_local
            and not backend.is_local
        ):
            raise OverrideRefused(
                f"force_backend {name!r} is cloud but content is "
                f"{sensitive.value}-sensitive; refused"
            )
        if _is_cloud_text(backend) and history_has_phi:
            raise OverrideRefused(
                f"force_backend {name!r} refused: chat history contains PHI; cloud text "
                "backend would include it in context. Start a new chat or pick Local."
            )
        cls = Classification(sensitivity=sensitive or Sensitivity.NONE)
        cls_dict = cls.model_dump(mode="json")
        cls_dict["source"] = "force_backend"
        sysprompt = _privacy_system_prompt(sensitive)
        return RoutingDecision(
            backend=backend,
            reason=f"force_backend → {backend.name}",
            classification=cls_dict,
            system_prompt=sysprompt,
            model_override=self._config.local_model_overrides.get(sensitive.value)
            if sensitive not in {None, Sensitivity.NONE}
            else None,
        )

    async def _cloud_override_sensitivity(
        self, text: str, target: Backend | None
    ) -> Sensitivity | None:
        """Classify explicit remote routes before honoring them.

        Regex checks in ``_apply_override`` remain a second layer. This
        classifier pass catches semantic medical/private content that has no
        obvious identifier pattern. A classifier outage refuses the remote
        route instead of silently treating the request as non-sensitive.
        """
        if target is None or target.is_local or self._classifier is None:
            return None
        bypass = can_bypass(text)
        if bypass is not None and bypass.sensitivity in (
            Sensitivity.PHI,
            Sensitivity.PHI_MEDICAL,
            Sensitivity.SECRET,
        ):
            return bypass.sensitivity
        try:
            classification = await self._classifier(text)
        except Exception as exc:
            if self._config.fail_closed_on_classifier_error:
                raise OverrideRefused(
                    "cloud route refused: privacy classifier unavailable; retry or use local"
                ) from exc
            return None
        if classification.sensitivity in (
            Sensitivity.PHI,
            Sensitivity.PHI_MEDICAL,
            Sensitivity.SECRET,
        ):
            return classification.sensitivity
        return None

    def _apply_rules(
        self,
        classification: Classification,
        source: str,
        original_text: str,
        *,
        spend_blocked: bool = False,
        history_has_phi: bool = False,
    ) -> RoutingDecision:
        cls_dict = classification.model_dump(mode="json")
        cls_dict["source"] = source

        sensitive = classification.sensitivity in (
            Sensitivity.PHI,
            Sensitivity.PHI_MEDICAL,
            Sensitivity.SECRET,
        )
        if sensitive and self._config.phi_must_be_local:
            backend = self._preferred_local()
            sysprompt = _privacy_system_prompt(classification.sensitivity)
            return RoutingDecision(
                backend=backend,
                reason=f"sensitivity={classification.sensitivity.value}; forced local ({backend.name})",
                classification=cls_dict,
                system_prompt=sysprompt,
                model_override=self._config.local_model_overrides.get(
                    classification.sensitivity.value
                ),
            )

        # Image gen routes to Stability - it only sends the current user prompt
        # (no history), so history_has_phi doesn't apply to this path.
        if classification.intent == Intent.IMAGE and "stability" in self._backends:
            if spend_blocked:
                source = source + "+spend-cap"
                cls_dict["source"] = source
            else:
                return RoutingDecision(
                    backend=self._backends["stability"],
                    reason=f"intent=image [{source}]",
                    classification=cls_dict,
                )

        # v0.2.14: needs_tools forces cloud-text route (Anthropic) when
        # tools are available - local Ollama doesn't yet have a tool loop.
        # Honors the same spend / history-PHI gates.
        wants_anthropic = (
            classification.complexity == Complexity.HIGH
            or classification.intent == Intent.COMPLEX_REASONING
            or classification.needs_tools
        )
        if wants_anthropic and "anthropic" in self._backends:
            if spend_blocked:
                source = source + "+spend-cap"
                cls_dict["source"] = source
            elif history_has_phi:
                source = source + "+history-phi"
                cls_dict["source"] = source
            else:
                return RoutingDecision(
                    backend=self._backends["anthropic"],
                    reason=f"complexity={classification.complexity.value} intent={classification.intent.value} [{source}]",
                    classification=cls_dict,
                    use_tools=classification.needs_tools,
                )

        lora = (
            self._config.lora_variants.get("code") if classification.intent == Intent.CODE else None
        )
        default = self._backends.get(self._config.default_backend) or self._first_local()

        # The default backend can be cloud (Settings / profiles allow it).
        # The same gates that protect the explicit-override and escalation
        # paths apply here too: PHI-tainted history must not ship to a cloud
        # text backend, and a blocked spend cap must not be billable via the
        # default route. Degrade to local rather than erroring - the user
        # didn't ask for cloud by name, so a silent-but-visible fallback
        # (reason string says why) beats a refusal.
        if _is_cloud_text(default) and (history_has_phi or spend_blocked):
            gates = []
            if history_has_phi:
                gates.append("history-phi")
            if spend_blocked:
                gates.append("spend-cap")
            fallback = self._preferred_local()
            cls_dict["source"] = source + "+" + "+".join(g + "-fallback" for g in gates)
            return RoutingDecision(
                backend=fallback,
                reason=(
                    f"default ({self._config.default_backend}) is cloud but "
                    f"{'+'.join(gates)} gate active; fell back to local ({fallback.name}) "
                    f"[{source}]"
                ),
                classification=cls_dict,
                lora_variant=lora,
            )

        why = f"default ({self._config.default_backend}) [{source}"
        if source == "rules-fast-path":
            why += "; short non-PHI non-image non-code"
        why += "]"
        return RoutingDecision(
            backend=default,
            reason=why,
            classification=cls_dict,
            lora_variant=lora,
        )

    def _fallback_classify(self, text: str) -> Classification:
        lowered = text.lower()
        intent = Intent.SIMPLE_QA
        if any(t in lowered for t in ("draw", "image of", "picture of", "/image")):
            intent = Intent.IMAGE
        elif any(
            t in lowered for t in ("```", "def ", "function ", "class ", "bug", "stack trace")
        ):
            intent = Intent.CODE
        complexity = (
            Complexity.HIGH
            if len(text) > self._config.long_message_token_threshold * 4
            else Complexity.LOW
        )
        return Classification(intent=intent, sensitivity=Sensitivity.NONE, complexity=complexity)

    def first_vision_backend(self) -> str | None:
        """Name of the preferred configured backend that can accept image
        attachments, or None if none is. Anthropic first for continuity with
        the old hardcoded behavior, then the other cloud vision backends in
        a stable order. Used by chat/stream.py when a message carries images
        and the user didn't force a backend."""
        for name in ("anthropic", "gemini", "openai", "openrouter"):
            b = self._backends.get(name)
            if b is not None and Capability.VISION in b.capabilities:
                return name
        return None

    def _preferred_local(self) -> Backend:
        default = self._backends.get(self._config.default_backend)
        if default is not None and default.is_local:
            return default
        for b in self._backends.values():
            if b.is_local and b.name != "echo":
                return b
        return self._first_local()

    def _first_local(self) -> Backend:
        for b in self._backends.values():
            if b.is_local:
                return b
        raise RuntimeError("no local backend registered")
