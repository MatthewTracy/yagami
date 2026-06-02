"""HTTP GET against an allowlist of hosts.

Why an allowlist instead of a blocklist: blocklists are unmanageable for a
local-first tool. The user opts into specific hosts they trust by editing
yagami.toml. Default allowlist is empty + Wikipedia (safe encyclopedic
read-only) so the skill demos out of the box.

PHI-gated: in any session with sensitivity != NONE, web.fetch refuses —
fetching pages can leak the URL (with PHI in the query string) to the
remote host's logs.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from ..router.schema import Sensitivity
from .base import Skill, SkillContext, SkillResult

_DEFAULT_ALLOWLIST = {
    "en.wikipedia.org",
    "en.m.wikipedia.org",
    "wikipedia.org",
}
_MAX_BYTES = 200_000  # truncate response to keep tool result LLM-friendly


def _strip_html(html: str) -> str:
    """Very lightweight HTML → text. Not a parser — drops tags and
    collapses whitespace. Good enough for tool results that the LLM will
    summarize. For richer extraction, swap to readability-lxml later."""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


class WebFetch:
    name = "web.fetch"
    description = (
        "Fetch a single URL and return its text content. Only allowlisted "
        "hosts are reachable (default: Wikipedia). Refuses to run in any "
        "session containing PHI or secrets. Returns up to 200KB of stripped text."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute https URL."},
        },
        "required": ["url"],
    }
    requires_network = True
    sensitivity_ceiling = Sensitivity.NONE  # PHI / secret sessions refuse

    def __init__(self, allowlist: set[str] | None = None) -> None:
        self._allowlist = allowlist or _DEFAULT_ALLOWLIST

    async def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        url = (args.get("url") or "").strip()
        if not url:
            return SkillResult(ok=False, error="missing 'url'")
        try:
            parsed = urlparse(url)
        except ValueError as exc:
            return SkillResult(ok=False, error=f"invalid URL: {exc}")
        if parsed.scheme != "https":
            return SkillResult(ok=False, error="only https:// URLs are allowed")
        host = (parsed.hostname or "").lower()
        if host not in self._allowlist:
            return SkillResult(
                ok=False,
                error=(f"host {host!r} not in allowlist. Allowed: {sorted(self._allowlist)}"),
            )

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                follow_redirects=True,
                headers={"User-Agent": "Yagami/0.2 (local-first AI orchestrator)"},
            ) as client:
                r = await client.get(url)
                r.raise_for_status()
                body = r.text[:_MAX_BYTES]
        except (httpx.HTTPError, ValueError) as exc:
            return SkillResult(ok=False, error=f"fetch failed: {exc}")

        stripped = _strip_html(body)
        truncated = len(stripped) >= _MAX_BYTES
        return SkillResult(
            ok=True,
            content=stripped[:_MAX_BYTES] + ("\n\n... [truncated]" if truncated else ""),
            artifacts={"url": url, "bytes": len(body)},
        )


def build() -> Skill:
    return WebFetch()
