"""Sentence-aware chunking for long observations.

Strategy:
- Target ~800 tokens per chunk with 100-token overlap.
- Cap at 8 chunks per message - anything longer probably belongs in a doc
  store, not the chat memory. The cap stops one runaway turn from
  monopolizing the embedding worker.
- Use 4 chars/token from `telemetry.costs.rough_token_count` as the size
  proxy - same heuristic the cost meter uses, so the budgets line up.
- Break on paragraph (\\n\\n) then on sentence (`. ` / `? ` / `! `) when
  possible, else hard-cut at the target boundary.
"""

from __future__ import annotations

import re

TARGET_TOKENS = 800
OVERLAP_TOKENS = 100
MAX_CHUNKS = 8
CHARS_PER_TOKEN = 4

_TARGET_CHARS = TARGET_TOKENS * CHARS_PER_TOKEN
_OVERLAP_CHARS = OVERLAP_TOKENS * CHARS_PER_TOKEN

# Match the END of a sentence (so we split AFTER it). Period/question/bang
# followed by whitespace and a capital is a good-enough boundary heuristic.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])")


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_END.split(text)
    return [p for p in parts if p.strip()]


def chunk(text: str) -> list[str]:
    """Return a list of 1..MAX_CHUNKS chunks. Empty input → []."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= _TARGET_CHARS:
        return [text]

    # First try paragraph-then-sentence packing.
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    units: list[str] = []
    for p in paragraphs:
        if len(p) <= _TARGET_CHARS:
            units.append(p)
        else:
            units.extend(_split_sentences(p))

    # Greedy pack units into chunks of <= _TARGET_CHARS.
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for u in units:
        u_len = len(u)
        if u_len > _TARGET_CHARS:
            # Single unit too big - hard-cut.
            if buf:
                chunks.append(" ".join(buf))
                buf, buf_len = [], 0
            for i in range(0, u_len, _TARGET_CHARS):
                chunks.append(u[i : i + _TARGET_CHARS])
                if len(chunks) >= MAX_CHUNKS:
                    return chunks
            continue
        if buf_len + u_len + 1 > _TARGET_CHARS:
            chunks.append(" ".join(buf))
            buf, buf_len = [], 0
            if len(chunks) >= MAX_CHUNKS:
                return chunks
        buf.append(u)
        buf_len += u_len + 1
    if buf:
        chunks.append(" ".join(buf))

    # Apply overlap: prepend the last _OVERLAP_CHARS of chunk N-1 to chunk N.
    if _OVERLAP_CHARS > 0 and len(chunks) > 1:
        overlapped: list[str] = [chunks[0]]
        for i in range(1, len(chunks)):
            tail = chunks[i - 1][-_OVERLAP_CHARS:]
            overlapped.append(tail + " " + chunks[i])
        chunks = overlapped

    return chunks[:MAX_CHUNKS]
