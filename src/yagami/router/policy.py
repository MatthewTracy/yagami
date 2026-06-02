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


@dataclass
class RoutingDecision:
    backend: Backend
    reason: str
    classification: dict
    lora_variant: str | None = None
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
    ) -> None:
        self._config = config
        self._backends = backends
        self._classifier = classifier

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
        classified independently — no sticky floor — but cloud TEXT backends
        (anthropic) are refused when history_has_phi, because the whole history
        is sent in those requests. Image gen (stability) ignores history and is
        always safe to route to.
        """
        last_text = next((m.content for m in reversed(history) if m.role == "user"), "")

        # 1. Slash-command override at the start of the message.
        override = parse_override(last_text)
        if override.forced_backend:
            if spend_blocked and override.forced_backend in ("anthropic", "stability"):
                raise OverrideRefused(
                    f"override refused: daily spend cap reached; {override.forced_backend!r} blocked"
                )
            return self._apply_override(override, last_text, history_has_phi=history_has_phi)

        # 2. Programmatic force_backend (WS field).
        if force_backend:
            if spend_blocked and force_backend in ("anthropic", "stability"):
                raise OverrideRefused(
                    f"force_backend refused: daily spend cap reached; {force_backend!r} blocked"
                )
            return self._apply_force_backend(
                force_backend, last_text, history_has_phi=history_has_phi
            )

        # 3. Rule-based fast-path bypass for high-confidence cases.
        bypass = can_bypass(last_text)
        if bypass is not None:
            return self._apply_rules(
                bypass, "rules-fast-path", last_text, history_has_phi=history_has_phi
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
    ) -> RoutingDecision:
        from .fast_path import _has_phi, _has_secret  # local import to avoid cycle

        sensitive: Sensitivity | None = None
        if _has_phi(override.stripped_text):
            sensitive = Sensitivity.PHI
        elif _has_secret(override.stripped_text):
            sensitive = Sensitivity.SECRET

        if sensitive and self._config.phi_must_be_local:
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
        # — the user's current prompt may be safe but we'd ship Jenny's note to
        # Claude alongside it. Image backends only see the current prompt, so
        # they're safe to route to.
        if backend.name == "anthropic" and history_has_phi:
            raise OverrideRefused(
                "override refused: chat history contains PHI; cloud text backend would "
                "include it in context. Start a new chat or use /local."
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
        *,
        history_has_phi: bool = False,
    ) -> RoutingDecision:
        from .fast_path import _has_phi, _has_secret  # local import to avoid cycle

        sensitive: Sensitivity | None = None
        if _has_phi(last_text):
            sensitive = Sensitivity.PHI
        elif _has_secret(last_text):
            sensitive = Sensitivity.SECRET

        backend = self._backends.get(name)
        if backend is None:
            raise OverrideRefused(f"force_backend {name!r} not registered")
        if sensitive and self._config.phi_must_be_local and not backend.is_local:
            raise OverrideRefused(
                f"force_backend {name!r} is cloud but content is "
                f"{sensitive.value}-sensitive; refused"
            )
        if backend.name == "anthropic" and history_has_phi:
            raise OverrideRefused(
                f"force_backend {name!r} refused: chat history contains PHI; cloud text "
                "backend would include it in context. Start a new chat or pick Local."
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

        # Image gen routes to Stability — it only sends the current user prompt
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
        # tools are available — local Ollama doesn't yet have a tool loop.
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
