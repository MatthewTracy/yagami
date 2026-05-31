"""Extract plain text from uploaded files.

Currently supports:
- text/plain (.txt, .log, .csv, ...)
- text/markdown (.md) — rendered to plain text
- application/pdf (.pdf) — pypdf text extraction
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

_MAX_CHARS_DEFAULT = 80_000


@dataclass
class ExtractedDoc:
    filename: str
    mime: str
    text: str
    truncated: bool
    error: Optional[str] = None


def _extract_pdf(blob: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(blob))
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            parts.append("")
    return "\n\n".join(p.strip() for p in parts if p.strip())


def _extract_markdown(blob: bytes) -> str:
    # Render the markdown to plain by stripping markup. For chat-context
    # purposes the raw markdown source is actually more useful than rendered
    # text — preserves structure (headings, lists, links). Just decode.
    return blob.decode("utf-8", errors="replace")


def _extract_text(blob: bytes) -> str:
    return blob.decode("utf-8", errors="replace")


def extract(
    *,
    filename: str,
    mime: str,
    blob: bytes,
    max_chars: int = _MAX_CHARS_DEFAULT,
) -> ExtractedDoc:
    low = (filename or "").lower()
    try:
        if mime == "application/pdf" or low.endswith(".pdf"):
            text = _extract_pdf(blob)
            mime = "application/pdf"
        elif mime in ("text/markdown", "text/x-markdown") or low.endswith((".md", ".markdown")):
            text = _extract_markdown(blob)
            mime = "text/markdown"
        elif mime.startswith("text/") or low.endswith(
            (".txt", ".log", ".csv", ".json", ".yaml", ".yml")
        ):
            text = _extract_text(blob)
            mime = mime or "text/plain"
        else:
            return ExtractedDoc(
                filename=filename,
                mime=mime,
                text="",
                truncated=False,
                error=f"unsupported file type {mime!r}",
            )
    except Exception as exc:
        return ExtractedDoc(filename=filename, mime=mime, text="", truncated=False, error=str(exc))

    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars] + f"\n\n... [truncated at {max_chars} chars]"
    return ExtractedDoc(filename=filename, mime=mime, text=text.strip(), truncated=truncated)
