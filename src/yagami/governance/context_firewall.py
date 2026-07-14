"""Content-free trust and indirect prompt-injection inspection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class TrustLevel(str, Enum):
    TRUSTED = "trusted"
    USER = "user"
    MODEL = "model"
    UNTRUSTED = "untrusted"


_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str], int], ...] = (
    (
        "instruction_override",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\b.{0,48}\b(?:previous|prior|system|developer|safety)\b.{0,24}\b(?:instruction|prompt|rule)s?\b",
            re.IGNORECASE | re.DOTALL,
        ),
        4,
    ),
    (
        "privilege_claim",
        re.compile(
            r"\b(?:you are now|new system message|developer message|highest priority|root access)\b",
            re.IGNORECASE,
        ),
        3,
    ),
    (
        "secret_exfiltration",
        re.compile(
            r"\b(?:reveal|print|return|send|upload|exfiltrate)\b.{0,64}\b(?:secret|credential|token|api key|system prompt|environment variable)s?\b",
            re.IGNORECASE | re.DOTALL,
        ),
        4,
    ),
    (
        "tool_coercion",
        re.compile(
            r"\b(?:call|invoke|execute|run|use)\b.{0,48}\b(?:tool|function|shell|command|payment|email|sql)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        2,
    ),
    (
        "concealment",
        re.compile(
            r"\b(?:do not|don't|never)\b.{0,32}\b(?:tell|mention|disclose|show)\b.{0,32}\b(?:user|operator|reviewer)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        2,
    ),
)


@dataclass(frozen=True)
class ContextInspection:
    signals: tuple[str, ...]
    score: int

    @property
    def suspicious(self) -> bool:
        return self.score >= 4 or len(self.signals) >= 2

    def summary(self) -> dict:
        return {
            "signals": list(self.signals),
            "score": self.score,
            "suspicious": self.suspicious,
        }


def inspect_context(text: str) -> ContextInspection:
    matches = [
        (name, weight) for name, pattern, weight in _INJECTION_PATTERNS if pattern.search(text)
    ]
    return ContextInspection(
        signals=tuple(name for name, _weight in matches),
        score=sum(weight for _name, weight in matches),
    )


def trust_for_message(*, role: str, content: str, is_current_user: bool) -> TrustLevel:
    if role == "tool":
        return TrustLevel.UNTRUSTED
    if role == "system":
        lowered = content.lstrip().lower()
        if lowered.startswith(("retrieved ", "retrieval ", "memory context", "document context")):
            return TrustLevel.UNTRUSTED
        return TrustLevel.TRUSTED
    if role == "assistant":
        return TrustLevel.MODEL
    if is_current_user:
        return TrustLevel.USER
    return TrustLevel.USER
