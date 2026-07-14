"""HTTP GET against an allowlist of hosts.

Why an allowlist instead of a blocklist: blocklists are unmanageable for a
local-first tool. The user opts into specific hosts they trust by editing
yagami.toml. Default allowlist is empty + Wikipedia (safe encyclopedic
read-only) so the skill demos out of the box.

PHI-gated: in any session with sensitivity != NONE, web.fetch refuses -
fetching pages can leak the URL (with PHI in the query string) to the
remote host's logs.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from ..router.schema import Sensitivity
from .base import Skill, SkillContext, SkillResult

_DEFAULT_ALLOWLIST = {
    "en.wikipedia.org",
    "en.m.wikipedia.org",
    "wikipedia.org",
}
_MAX_BYTES = 200_000  # truncate response to keep tool result LLM-friendly
_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class _HTMLTextExtractor(HTMLParser):
    _IGNORED = {"script", "style", "template", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        normalized = tag.casefold()
        if normalized in self._IGNORED:
            self._ignored_stack.append(normalized)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in self._IGNORED and normalized in self._ignored_stack:
            index = len(self._ignored_stack) - 1 - self._ignored_stack[::-1].index(normalized)
            del self._ignored_stack[index:]

    def handle_data(self, data: str) -> None:
        if not self._ignored_stack:
            self.parts.append(data)


def _strip_html(html: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    parser.close()
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


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

    def __init__(
        self,
        allowlist: set[str] | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._allowlist = allowlist if allowlist is not None else _DEFAULT_ALLOWLIST
        self._transport = transport

    def _validate_url(self, url: str) -> str | None:
        try:
            parsed = urlparse(url)
        except ValueError as exc:
            return f"invalid URL: {exc}"
        if parsed.scheme != "https":
            return "only https:// URLs are allowed"
        host = (parsed.hostname or "").lower()
        if host not in self._allowlist:
            return f"host {host!r} not in allowlist. Allowed: {sorted(self._allowlist)}"
        return None

    async def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        url = (args.get("url") or "").strip()
        if not url:
            return SkillResult(ok=False, error="missing 'url'")
        validation_error = self._validate_url(url)
        if validation_error:
            return SkillResult(ok=False, error=validation_error)

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                follow_redirects=False,
                headers={"User-Agent": "Yagami/0.2 (local-first AI orchestrator)"},
                transport=self._transport,
            ) as client:
                current_url = url
                redirect_count = 0
                while True:
                    validation_error = self._validate_url(current_url)
                    if validation_error:
                        return SkillResult(
                            ok=False,
                            error=f"redirect refused: {validation_error}",
                        )
                    async with client.stream("GET", current_url) as response:
                        if response.status_code in _REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if not location:
                                return SkillResult(
                                    ok=False, error="redirect missing Location header"
                                )
                            if redirect_count >= _MAX_REDIRECTS:
                                return SkillResult(ok=False, error="too many redirects")
                            current_url = urljoin(current_url, location)
                            redirect_count += 1
                            continue

                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "text/plain")
                        media_type = content_type.split(";", 1)[0].strip().casefold()
                        if media_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
                            return SkillResult(
                                ok=False,
                                error=f"unsupported response content type {media_type!r}",
                            )
                        encoding = response.charset_encoding or "utf-8"
                        body_bytes = bytearray()
                        response_truncated = False
                        async for chunk in response.aiter_bytes():
                            remaining = _MAX_BYTES - len(body_bytes)
                            if len(chunk) > remaining:
                                body_bytes.extend(chunk[:remaining])
                                response_truncated = True
                                break
                            body_bytes.extend(chunk)
                        body = bytes(body_bytes).decode(encoding, errors="replace")
                        break
        except (httpx.HTTPError, ValueError) as exc:
            return SkillResult(ok=False, error=f"fetch failed: {exc}")

        stripped = _strip_html(body)
        truncated = response_truncated or len(stripped) > _MAX_BYTES
        return SkillResult(
            ok=True,
            content=stripped[:_MAX_BYTES] + ("\n\n... [truncated]" if truncated else ""),
            artifacts={
                "url": url,
                "final_url": current_url,
                "bytes": len(body_bytes),
                "redirects": redirect_count,
            },
        )


def build() -> Skill:
    return WebFetch()
