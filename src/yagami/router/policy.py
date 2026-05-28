from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from ..backends.base import Backend, Message
from ..config import RoutingConfig
from .schema import Classification, Complexity, Intent, Sensitivity

Classifier = Callable[[list[Message]], Awaitable[Classification]]


@dataclass
class RoutingDecision:
    backend: Backend
    reason: str
    classification: dict
    lora_variant: str | None = None


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

    async def decide(self, history: list[Message]) -> RoutingDecision:
        if self._classifier is None:
            classification = self._fallback_classify(history)
            source = "fallback"
        else:
            try:
                classification = await self._classifier(history)
                source = "classifier"
            except Exception:
                classification = self._fallback_classify(history)
                source = "fallback-after-error"

        return self._apply_rules(classification, source)

    def _apply_rules(self, classification: Classification, source: str) -> RoutingDecision:
        cls_dict = classification.model_dump(mode="json")
        cls_dict["source"] = source

        if (
            classification.sensitivity in (Sensitivity.PHI, Sensitivity.PHI_MEDICAL)
            and self._config.phi_must_be_local
        ):
            backend = self._preferred_local()
            lora = self._config.lora_variants.get("phi_medical") if classification.sensitivity == Sensitivity.PHI_MEDICAL else None
            return RoutingDecision(
                backend=backend,
                reason=f"sensitivity={classification.sensitivity.value}; forced local ({backend.name})",
                classification=cls_dict,
                lora_variant=lora,
            )

        if classification.intent == Intent.IMAGE and "stability" in self._backends:
            return RoutingDecision(
                backend=self._backends["stability"],
                reason="intent=image",
                classification=cls_dict,
            )

        if (
            classification.complexity == Complexity.HIGH
            or classification.intent == Intent.COMPLEX_REASONING
        ) and "anthropic" in self._backends:
            return RoutingDecision(
                backend=self._backends["anthropic"],
                reason=f"complexity={classification.complexity.value} intent={classification.intent.value}",
                classification=cls_dict,
            )

        lora = self._config.lora_variants.get("code") if classification.intent == Intent.CODE else None
        default = self._backends.get(self._config.default_backend) or self._first_local()
        return RoutingDecision(
            backend=default,
            reason=f"default ({self._config.default_backend})",
            classification=cls_dict,
            lora_variant=lora,
        )

    def _fallback_classify(self, history: list[Message]) -> Classification:
        last = next((m.content for m in reversed(history) if m.role == "user"), "")
        lowered = last.lower()
        intent = Intent.SIMPLE_QA
        if any(t in lowered for t in ("draw", "image of", "picture of", "/image")):
            intent = Intent.IMAGE
        elif any(t in lowered for t in ("```", "def ", "function ", "class ", "bug", "stack trace")):
            intent = Intent.CODE
        complexity = Complexity.HIGH if len(last) > self._config.long_message_token_threshold * 4 else Complexity.LOW
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
