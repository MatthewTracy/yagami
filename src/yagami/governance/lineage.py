from __future__ import annotations

import hashlib
import json
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field

from ..backends.base import Message
from .transform import detect_entity_types
from .context_firewall import TrustLevel, inspect_context, trust_for_message
from ..router.fast_path import _has_phi, _has_secret
from ..router.policy import stickier
from ..router.schema import Sensitivity


class LineageSource(str, Enum):
    USER = "user"
    HISTORY = "history"
    SYSTEM = "system"
    IMAGE = "image"
    RETRIEVAL = "retrieval"
    MEMORY = "memory"
    TOOL_ARGUMENT = "tool_argument"
    TOOL_RESULT = "tool_result"
    OUTPUT = "output"


class LineageItem(BaseModel):
    id: str
    source: LineageSource
    role: str | None = None
    sensitivity: Sensitivity
    detector: str
    trust: TrustLevel
    injection_signals: list[str] = Field(default_factory=list)
    injection_score: int = 0
    content_fingerprint: str
    parents: list[str] = Field(default_factory=list)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


def _rules_sensitivity(text: str) -> Sensitivity:
    entity_types = set(detect_entity_types(text))
    if _has_secret(text) or entity_types.intersection({"API_KEY", "AWS_KEY", "JWT"}):
        return Sensitivity.SECRET
    if _has_phi(text) or entity_types:
        return Sensitivity.PHI
    return Sensitivity.NONE


class LineageGraph:
    """Content-free provenance graph: labels and salted fingerprints only."""

    def __init__(self, *, request_id: str) -> None:
        self.request_id = request_id
        self.items: list[LineageItem] = []

    def _fingerprint(self, content: str) -> str:
        return hashlib.sha256(f"{self.request_id}:{content}".encode("utf-8")).hexdigest()[:24]

    def add(
        self,
        *,
        source: LineageSource,
        content: str,
        sensitivity: Sensitivity,
        detector: str,
        trust: TrustLevel = TrustLevel.UNTRUSTED,
        role: str | None = None,
        parents: list[str] | None = None,
        metadata: dict[str, str | int | float | bool | None] | None = None,
    ) -> LineageItem:
        context_inspection = inspect_context(content)
        item = LineageItem(
            id="lin_" + uuid4().hex[:16],
            source=source,
            role=role,
            sensitivity=sensitivity,
            detector=detector,
            trust=trust,
            injection_signals=list(context_inspection.signals),
            injection_score=context_inspection.score,
            content_fingerprint=self._fingerprint(content),
            parents=parents or [],
            metadata=metadata or {},
        )
        self.items.append(item)
        return item

    @classmethod
    def from_messages(
        cls,
        *,
        request_id: str,
        messages: list[Message],
        current_sensitivity: Sensitivity,
        caller_hint: Sensitivity | None,
    ) -> "LineageGraph":
        graph = cls(request_id=request_id)
        last_user = max(
            (index for index, message in enumerate(messages) if message.role == "user"),
            default=-1,
        )
        prior_ids: list[str] = []
        for index, message in enumerate(messages):
            if message.role == "system":
                source = LineageSource.SYSTEM
            elif message.role == "user" and index == last_user:
                source = LineageSource.USER
            else:
                source = LineageSource.HISTORY

            if index == last_user:
                sensitivity = stickier(
                    stickier(current_sensitivity, caller_hint),
                    _rules_sensitivity(message.content),
                )
                detector = "router+rules+caller-hint" if caller_hint else "router+rules"
            else:
                sensitivity = _rules_sensitivity(message.content)
                detector = "rules"
            item = graph.add(
                source=source,
                content=message.content,
                sensitivity=sensitivity,
                detector=detector,
                trust=trust_for_message(
                    role=message.role,
                    content=message.content,
                    is_current_user=index == last_user,
                ),
                role=message.role,
                parents=list(prior_ids[-1:]),
            )
            prior_ids.append(item.id)
            if message.tool_calls:
                tool_call_content = json.dumps(
                    message.tool_calls,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                tool_item = graph.add(
                    source=LineageSource.TOOL_ARGUMENT,
                    content=tool_call_content,
                    sensitivity=_rules_sensitivity(tool_call_content),
                    detector="rules",
                    trust=TrustLevel.MODEL,
                    role=message.role,
                    parents=[item.id],
                    metadata={"kind": "function_arguments", "count": len(message.tool_calls)},
                )
                prior_ids.append(tool_item.id)
            for image_index, image in enumerate(message.images or []):
                image_item = graph.add(
                    source=LineageSource.IMAGE,
                    content=image.data_b64,
                    sensitivity=sensitivity,
                    detector="inherited",
                    trust=trust_for_message(
                        role=message.role,
                        content=message.content,
                        is_current_user=index == last_user,
                    ),
                    role=message.role,
                    parents=[item.id],
                    metadata={"media_type": image.media_type, "index": image_index},
                )
                prior_ids.append(image_item.id)
        return graph

    @property
    def effective_sensitivity(self) -> Sensitivity:
        current = Sensitivity.NONE
        for item in self.items:
            current = stickier(current, item.sensitivity)
        return current

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        trust_counts: dict[str, int] = {}
        for item in self.items:
            counts[item.sensitivity.value] = counts.get(item.sensitivity.value, 0) + 1
            trust_counts[item.trust.value] = trust_counts.get(item.trust.value, 0) + 1
        return {
            "effective_sensitivity": self.effective_sensitivity.value,
            "counts": counts,
            "trust_counts": trust_counts,
            "untrusted_injection": self.has_untrusted_injection,
            "injection_signals": sorted(
                {
                    signal
                    for item in self.items
                    if item.trust == TrustLevel.UNTRUSTED
                    for signal in item.injection_signals
                }
            ),
            "items": [item.model_dump(mode="json") for item in self.items],
        }

    @property
    def has_untrusted_injection(self) -> bool:
        return any(
            item.trust == TrustLevel.UNTRUSTED
            and (item.injection_score >= 4 or len(item.injection_signals) >= 2)
            for item in self.items
        )
