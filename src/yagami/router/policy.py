from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from ..backends.base import Backend, Message
from ..config import RoutingConfig
from .fast_path import can_bypass
from .overrides import OverrideResult, parse as parse_override
from .prompts import PHI_MEDICAL_SYSTEM_PROMPT
from .schema import Classification, Complexity, Intent, Sensitivity

Classifier = Callable[[str], Awaitable[Classification]]

# Order of "stickiness". Once a session is elevated, it never drops back to
# none — this prevents the bug where a long PHI conversation has a short
# "summarize" turn classify as none and leak to a cloud backend.
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


@dataclass
class RoutingDecision:
    backend: Backend
    reason: str
    classification: dict
    lora_variant: str | None = None
    system_prompt: str | None = None
    effective_user_text: str | None = None  # set when override stripped a prefix


class RoutingPolicy:
    def __init__(
        self,
        *,
        config: RoutingConfig,
        backends: dict[str, Backend],
        classifier: Classifier | None = None,
    ) -> None:
        self._config = config
        self._backends = backends
        self._classifier = classifier

    async def decide(
        self,
        history: list[Message],
        *,
        force_backend: str | None = None,
        sensitivity_floor: Sensitivity | None = None,
    ) -> RoutingDecision:
        last_text = next((m.content for m in reversed(history) if m.role == "user"), "")

        # 1. Slash-command override at the start of the message.
        override = parse_override(last_text)
        if override.forced_backend:
            return self._apply_override(override, last_text, sensitivity_floor)

        # 2. Programmatic force_backend (WS field).
        if force_backend:
            return self._apply_force_backend(force_backend, last_text, sensitivity_floor)

        # 3. Rule-based fast-path bypass for high-confidence cases.
        bypass = can_bypass(last_text)
        if bypass is not None:
            return self._apply_rules(
                self._apply_floor(bypass, sensitivity_floor),
                self._floor_source("rules-fast-path", bypass.sensitivity, sensitivity_floor),
                last_text,
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
                source = "fallback-after-error"

        floored = self._apply_floor(classification, sensitivity_floor)
        source = self._floor_source(source, classification.sensitivity, sensitivity_floor)
        return self._apply_rules(floored, source, last_text)

    def _apply_floor(
        self, classification: Classification, floor: Sensitivity | None
    ) -> Classification:
        if floor is None:
            return classification
        new_sens = stickier(classification.sensitivity, floor)
        if new_sens == classification.sensitivity:
            return classification
        return Classification(
            intent=classification.intent,
            sensitivity=new_sens,
            complexity=classification.complexity,
        )

    @staticmethod
    def _floor_source(base: str, raw: Sensitivity, floor: Sensitivity | None) -> str:
        if floor is None:
            return base
        if stickier(raw, floor) != raw:
            return f"{base}+floor"
        return base

    def _apply_override(
        self,
        override: OverrideResult,
        original_text: str,
        sensitivity_floor: Sensitivity | None = None,
    ) -> RoutingDecision:
        # Classify the stripped text so PHI/SECRET guard still bites.
        # We don't await the LLM classifier here — only run regex/rules to keep
        # the override fast. The fast-path covers PHI/SECRET via _has_phi/_has_secret.
        from .fast_path import _has_phi, _has_secret  # local import to avoid cycle

        sensitive: Sensitivity | None = None
        if _has_phi(override.stripped_text):
            sensitive = Sensitivity.PHI
        elif _has_secret(override.stripped_text):
            sensitive = Sensitivity.SECRET
        # Apply session floor so a long PHI session can't be downgraded just
        # by sending a short non-PHI override.
        if sensitivity_floor is not None:
            sensitive = stickier(sensitive, sensitivity_floor)
            if sensitive == Sensitivity.NONE:
                sensitive = None

        if sensitive and self._config.phi_must_be_local:
            target_backend_name = override.forced_backend
            target = self._backends.get(target_backend_name or "")
            if target is not None and not target.is_local:
                raise OverrideRefused(
                    f"override ignored: sensitivity={sensitive.value} requires local backend, "
                    f"requested {target_backend_name!r} is cloud"
                )

        backend = self._backends.get(override.forced_backend or "")
        if backend is None:
            raise OverrideRefused(f"override backend {override.forced_backend!r} not available")

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
        sysprompt = PHI_MEDICAL_SYSTEM_PROMPT if sensitive == Sensitivity.PHI_MEDICAL else None
        return RoutingDecision(
            backend=backend,
            reason=f"slash override → {backend.name}",
            classification=cls_dict,
            system_prompt=sysprompt,
            effective_user_text=override.stripped_text or None,
        )

    def _apply_force_backend(
        self,
        name: str,
        last_text: str,
        sensitivity_floor: Sensitivity | None = None,
    ) -> RoutingDecision:
        from .fast_path import _has_phi, _has_secret  # local import to avoid cycle

        sensitive: Sensitivity | None = None
        if _has_phi(last_text):
            sensitive = Sensitivity.PHI
        elif _has_secret(last_text):
            sensitive = Sensitivity.SECRET
        if sensitivity_floor is not None:
            sensitive = stickier(sensitive, sensitivity_floor)
            if sensitive == Sensitivity.NONE:
                sensitive = None

        backend = self._backends.get(name)
        if backend is None:
            raise OverrideRefused(f"force_backend {name!r} not registered")
        if sensitive and self._config.phi_must_be_local and not backend.is_local:
            raise OverrideRefused(
                f"force_backend {name!r} is cloud but content is "
                f"{sensitive.value}-sensitive; refused"
            )
        cls = Classification(sensitivity=sensitive or Sensitivity.NONE)
        cls_dict = cls.model_dump(mode="json")
        cls_dict["source"] = "force_backend"
        sysprompt = PHI_MEDICAL_SYSTEM_PROMPT if sensitive == Sensitivity.PHI_MEDICAL else None
        return RoutingDecision(
            backend=backend,
            reason=f"force_backend → {backend.name}",
            classification=cls_dict,
            system_prompt=sysprompt,
        )

    def _apply_rules(
        self, classification: Classification, source: str, original_text: str
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
            sysprompt = (
                PHI_MEDICAL_SYSTEM_PROMPT
                if classification.sensitivity == Sensitivity.PHI_MEDICAL
                else None
            )
            return RoutingDecision(
                backend=backend,
                reason=f"sensitivity={classification.sensitivity.value}; forced local ({backend.name})",
                classification=cls_dict,
                system_prompt=sysprompt,
            )

        if classification.intent == Intent.IMAGE and "stability" in self._backends:
            return RoutingDecision(
                backend=self._backends["stability"],
                reason=f"intent=image [{source}]",
                classification=cls_dict,
            )

        if (
            classification.complexity == Complexity.HIGH
            or classification.intent == Intent.COMPLEX_REASONING
        ) and "anthropic" in self._backends:
            return RoutingDecision(
                backend=self._backends["anthropic"],
                reason=f"complexity={classification.complexity.value} intent={classification.intent.value} [{source}]",
                classification=cls_dict,
            )

        lora = (
            self._config.lora_variants.get("code") if classification.intent == Intent.CODE else None
        )
        default = self._backends.get(self._config.default_backend) or self._first_local()
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
