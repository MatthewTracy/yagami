"""Rule-based classifier bypass for the common-case short prompts.

If `can_bypass()` returns a Classification, the policy can skip the LLM classifier
entirely. Returns None whenever the prompt MIGHT need real classification — anything
not provably safe falls through to the LLM.
"""
from __future__ import annotations

import re

from ..telemetry.decisions import _PHI_PATTERNS
from .schema import Classification, Complexity, Intent, Sensitivity

_MAX_BYPASS_CHARS = 200

_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs)_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\b(?:password|api[_-]?key|secret)\s*[:=]\s*\S{8,}", re.IGNORECASE),
]

_CLINICAL_KEYWORDS = re.compile(
    r"\b("
    r"patient|pt\s+(?:reports?|presents?|states?|denies)|"
    r"diagnos[ei]s|symptoms?|prescrib[ei]|prescription|medication|"
    r"icd|dea|mrn|dob|phn|psa|bnp|hgb|a1c|bmi|crp|ldl|hdl|tsh|inr|prbc|bmp|"
    r"mri|ct\s+scan|biopsy|hospital(?:ization)?|clinic|cardiology|"
    r"oncology|psychiatr(?:ic|y)|surger(?:y|ies)|rx\b|"
    r"suicide|suicidal|overdose|psychotic|5150|"
    r"hiv|aids|"
    r"sertraline|fluoxetine|metformin|lisinopril|atorvastatin|furosemide|"
    r"insulin|oxycodone|biktarvy|chemo(?:therapy)?|"
    r"\w+statin|\w+pril|\w+sartan|\w+vir|"
    r"brca\d?|tumor|tumour|cancer|carcinoma|leukemia|lymphoma|"
    r"hypertensi(?:on|ve)|htn|t[12]dm|chf|copd|nsclc|acei|"
    r"\d+\s*w\s*\d+\s*d|g\d+p\d+|"
    r"address\s+\d|deliver\s+supplies"
    r")\b",
    re.IGNORECASE,
)

# Lab-value shape: 2-4 uppercase letters followed by a number (BP 158, Na 128, BNP 612).
_LAB_VALUE_PATTERN = re.compile(r"\b[A-Z]{2,4}\s*[:=]?\s*\d+(?:\.\d+)?\b")

_CODE_MARKERS = (
    "```",
    "`",
    "def ",
    "class ",
    "function ",
    "import ",
    "console.",
    "npm ",
    "cargo ",
    "git ",
    "=>",
    "traceback",
    "exception",
    "stack trace",
    "fix this",
    "why does",
    "doesn't work",
    "not working",
)

_CODE_REGEX = re.compile(r";\s*$", re.MULTILINE)

_IMAGE_MARKERS = ("draw", "image of", "picture of", "/image", "generate an image", "paint")

# Imperative "give me / show me / create a / generate the / ..." phrasings
# almost always imply intent worth classifying (image, code, file, fetch).
# Falling through to the LLM also handles typos in image keywords (e.g.
# "give me a piocture" — fast-path can't fix that, but classifier can).
_IMPERATIVE_PATTERN = re.compile(
    r"\b(give|show|make|build|create|generate|fetch|find|render|design|paint|draw|sketch)\s+"
    r"(?:me\s+)?(?:a|an|the|some|me)\b",
    re.IGNORECASE,
)


def _has_secret(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_PATTERNS)


def _has_phi(text: str) -> bool:
    if any(p.search(text) for p in _PHI_PATTERNS):
        return True
    if _CLINICAL_KEYWORDS.search(text):
        return True
    if _LAB_VALUE_PATTERN.search(text):
        return True
    return False


def _has_code(text: str) -> bool:
    lowered = text.lower()
    if any(marker in lowered for marker in _CODE_MARKERS):
        return True
    return bool(_CODE_REGEX.search(text))


def _has_image_keyword(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _IMAGE_MARKERS)


def _is_imperative_request(text: str) -> bool:
    return bool(_IMPERATIVE_PATTERN.search(text))


def can_bypass(text: str) -> Classification | None:
    """Return a SIMPLE_QA classification if we can prove the LLM classifier adds nothing.

    Falls through (returns None) whenever the text:
    - is long enough to plausibly contain something interesting
    - matches PHI / secret / code / image keyword regex
    """
    if not text or len(text) >= _MAX_BYPASS_CHARS:
        return None
    if _has_phi(text):
        return None
    if _has_secret(text):
        return None
    if _has_code(text):
        return None
    if _has_image_keyword(text):
        return None
    if _is_imperative_request(text):
        return None
    return Classification(
        intent=Intent.SIMPLE_QA,
        sensitivity=Sensitivity.NONE,
        complexity=Complexity.LOW,
    )
