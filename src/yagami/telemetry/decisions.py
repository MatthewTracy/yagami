from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger("yagami.decisions")

_PHI_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b\d{16}\b"),
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    re.compile(r"\b\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
]


def scrub(text: str) -> str:
    out = text
    for pat in _PHI_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


def log_decision(
    *,
    session_id: str,
    user_text: str,
    decision: dict,
    log_path: Path | None = None,
) -> None:
    record = {
        "session_id": session_id,
        "user_text_preview": scrub(user_text[:200]),
        "decision": decision,
    }
    line = json.dumps(record)
    log.info("routing_decision %s", line)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
