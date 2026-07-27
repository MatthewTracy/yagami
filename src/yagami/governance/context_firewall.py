"""Content-free trust and indirect prompt-injection inspection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence, runtime_checkable


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
    (
        "prompt_disclosure",
        re.compile(
            r"\b(?:reveal|repeat|quote|show|print)\b.{0,48}\b(?:system|developer|hidden)\b.{0,24}\b(?:prompt|instruction|message)s?\b",
            re.IGNORECASE | re.DOTALL,
        ),
        4,
    ),
    (
        "role_impersonation",
        re.compile(
            r"(?:<|\[|###\s*)(?:system|developer|assistant)(?:>|\]|(?:\s+message)?\s*:)",
            re.IGNORECASE,
        ),
        3,
    ),
    (
        "encoded_instruction",
        re.compile(
            r"\b(?:base64|rot13|hex|decode|deobfuscate)\b.{0,48}\b(?:instruction|prompt|secret|token|credential)s?\b",
            re.IGNORECASE | re.DOTALL,
        ),
        3,
    ),
    (
        "multilingual_override",
        re.compile(
            r"(?:"
            r"\bignora\b.{0,48}\b(?:instrucciones|anteriores|sistema)\b|"
            r"\bignorez?\b.{0,48}\b(?:instructions|précédentes|système)\b|"
            r"\bignoriere\b.{0,48}\b(?:anweisungen|vorherigen|system)\b|"
            r"\bignore\b.{0,48}\b(?:instruções|anteriores|sistema)\b"
            r")",
            re.IGNORECASE | re.DOTALL,
        ),
        4,
    ),
)

_INVISIBLE_CONTROL_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")


@dataclass(frozen=True)
class DetectorSignal:
    """A content-free detector result safe to place in policy evidence."""

    name: str
    weight: int
    detector: str


@runtime_checkable
class ContentDetector(Protocol):
    """Extension point for local or remote content-risk detectors.

    Implementations return identifiers and numeric weights only. They must
    never place matched text in a signal because summaries are persisted in
    Yagami's content-free evidence.
    """

    name: str
    version: str

    def inspect(self, text: str) -> Sequence[DetectorSignal]: ...


class RegexInjectionDetector:
    name = "builtin-injection"
    version = "2"

    def inspect(self, text: str) -> Sequence[DetectorSignal]:
        return [
            DetectorSignal(name=signal, weight=weight, detector=self.name)
            for signal, pattern, weight in _INJECTION_PATTERNS
            if pattern.search(text)
        ]


class StructuralRiskDetector:
    name = "builtin-structural"
    version = "1"

    def inspect(self, text: str) -> Sequence[DetectorSignal]:
        signals: list[DetectorSignal] = []
        if _INVISIBLE_CONTROL_RE.search(text):
            signals.append(DetectorSignal("invisible_unicode", 3, self.name))
        if len(text) >= 100_000:
            signals.append(DetectorSignal("context_bloat", 3, self.name))
        nonempty_lines = [line.strip() for line in text.splitlines() if line.strip()]
        if nonempty_lines:
            counts: dict[str, int] = {}
            for line in nonempty_lines:
                counts[line] = counts.get(line, 0) + 1
            if max(counts.values()) >= 12:
                signals.append(DetectorSignal("repeated_instruction_bloat", 2, self.name))
        return signals


DEFAULT_DETECTORS: tuple[ContentDetector, ...] = (
    RegexInjectionDetector(),
    StructuralRiskDetector(),
)


@dataclass(frozen=True)
class ContextInspection:
    signals: tuple[str, ...]
    score: int
    detectors: tuple[str, ...] = ()

    @property
    def suspicious(self) -> bool:
        return self.score >= 4 or len(self.signals) >= 2

    def summary(self) -> dict:
        return {
            "signals": list(self.signals),
            "score": self.score,
            "suspicious": self.suspicious,
            "detectors": list(self.detectors),
        }


class ContextFirewall:
    def __init__(self, detectors: Sequence[ContentDetector] = DEFAULT_DETECTORS) -> None:
        self.detectors = tuple(detectors)
        if not self.detectors:
            raise ValueError("context firewall requires at least one detector")

    def inspect(self, text: str) -> ContextInspection:
        matches = [signal for detector in self.detectors for signal in detector.inspect(text)]
        names = tuple(dict.fromkeys(signal.name for signal in matches))
        detector_versions = tuple(
            f"{detector.name}@{detector.version}" for detector in self.detectors
        )
        return ContextInspection(
            signals=names,
            score=sum(signal.weight for signal in matches),
            detectors=detector_versions,
        )


_DEFAULT_FIREWALL = ContextFirewall()


def inspect_context(
    text: str, *, detectors: Sequence[ContentDetector] | None = None
) -> ContextInspection:
    firewall = _DEFAULT_FIREWALL if detectors is None else ContextFirewall(detectors)
    return firewall.inspect(text)


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
