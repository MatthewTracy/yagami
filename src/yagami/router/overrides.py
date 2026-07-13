"""Slash-command parsing for explicit user routing overrides.

If the user starts their message with one of the recognized commands, the
classifier is skipped and the named backend is selected directly. PHI / SECRET
guard still applies - see policy.py.

A fixed set of aliases (/cloud, /local, /image, /think, /code, /reset) map to
specific backends or hints. Beyond those, `/<name>` matches directly against
whatever backend names are currently registered (passed in by the caller) -
so a new backend module (e.g. `mistral.py`, `groq.py`) is immediately
reachable as `/mistral` / `/groq` with no change needed here. This is what
makes `/openai` (and any future backend) actually work, instead of requiring
a new alias per backend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

_LEADING_COMMAND = re.compile(r"^/(\w[\w.-]*)\b\s*", re.IGNORECASE)


@dataclass
class OverrideResult:
    forced_backend: str | None  # a registered backend name, or None
    hint_intent: str | None  # "code", "image", or None
    hint_complex: bool  # True if /think
    stripped_text: str  # user text with the command prefix removed
    bypass_history_phi: bool = False  # /reset - one-shot opt-out of history-PHI gate


def parse(text: str, backend_names: Iterable[str] = ()) -> OverrideResult:
    """Parse a leading slash command off the user message.

    `backend_names` should be the currently-registered backend names (e.g.
    `policy._backends.keys()`) so `/<name>` can resolve generically. Callers
    that don't pass it still get the fixed aliases below, just not the
    generic per-backend match.
    """
    m = _LEADING_COMMAND.match(text)
    if not m:
        return OverrideResult(None, None, False, text)
    cmd = m.group(1).lower()
    remaining = text[m.end() :].lstrip()
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
    if cmd == "reset":
        # One-shot bypass of the history-PHI check for THIS turn only. The
        # next turn re-evaluates. Doesn't force a backend - normal routing
        # applies to the stripped message.
        return OverrideResult(None, None, False, remaining, bypass_history_phi=True)
    by_name = {n.lower(): n for n in backend_names}
    if cmd in by_name:
        return OverrideResult(by_name[cmd], None, False, remaining)
    return OverrideResult(None, None, False, text)
