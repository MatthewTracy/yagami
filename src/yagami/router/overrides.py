"""Slash-command parsing for explicit user routing overrides.

If the user starts their message with one of the recognized commands, the
classifier is skipped and the named backend is selected directly. PHI / SECRET
guard still applies — see policy.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERN = re.compile(r"^/(cloud|claude|local|ollama|image|think|code)\b\s*", re.IGNORECASE)


@dataclass
class OverrideResult:
    forced_backend: str | None  # one of "anthropic", "ollama", "stability", or None
    hint_intent: str | None     # "code", "image", or None
    hint_complex: bool          # True if /think
    stripped_text: str          # user text with the command prefix removed


def parse(text: str) -> OverrideResult:
    """Parse a leading slash command off the user message."""
    m = _PATTERN.match(text)
    if not m:
        return OverrideResult(None, None, False, text)
    cmd = m.group(1).lower()
    remaining = text[m.end():].lstrip()
    if cmd in ("cloud", "claude"):
        return OverrideResult("anthropic", None, False, remaining)
    if cmd in ("local", "ollama"):
        return OverrideResult("ollama", None, False, remaining)
    if cmd == "image":
        return OverrideResult("stability", "image", False, remaining)
    if cmd == "think":
        return OverrideResult("anthropic", None, True, remaining)
    if cmd == "code":
        return OverrideResult("ollama", "code", False, remaining)
    return OverrideResult(None, None, False, text)
