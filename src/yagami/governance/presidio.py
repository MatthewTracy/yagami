"""Optional Microsoft Presidio REST adapter for enterprise PII detection."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..router.schema import Sensitivity

log = logging.getLogger("yagami.presidio")
_MAX_ANALYSIS_CHARS = 1_000_000


class PresidioInspector:
    def __init__(
        self,
        url: str,
        *,
        language: str = "en",
        score_threshold: float = 0.5,
        timeout_seconds: float = 5.0,
        fail_closed: bool = True,
        bearer_token: str = "",
        allow_remote: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(url)
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Presidio URL must be an absolute HTTP(S) URL")
        if not loopback and not allow_remote:
            raise ValueError(
                "remote Presidio requires YAGAMI_PRESIDIO_ALLOW_REMOTE=true; "
                "input text is sent to this service"
            )
        if not loopback and parsed.scheme != "https":
            raise ValueError("remote Presidio must use HTTPS")
        if not len(language) == 2 or not language.isalpha():
            raise ValueError("Presidio language must be a two-letter ISO 639-1 code")
        self.url = url.rstrip("/") + "/analyze"
        self.language = language.casefold()
        self.score_threshold = score_threshold
        self.timeout_seconds = timeout_seconds
        self.fail_closed = fail_closed
        self.bearer_token = bearer_token
        self.transport = transport

    async def inspect(self, text: str) -> Sensitivity:
        if not text:
            return Sensitivity.NONE
        if len(text) > _MAX_ANALYSIS_CHARS:
            if self.fail_closed:
                return Sensitivity.PHI
            text = text[:_MAX_ANALYSIS_CHARS]
        headers = {"Authorization": f"Bearer {self.bearer_token}"} if self.bearer_token else {}
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    self.url,
                    headers=headers,
                    json={
                        "text": text,
                        "language": self.language,
                        "score_threshold": self.score_threshold,
                        "return_decision_process": False,
                    },
                )
                response.raise_for_status()
                value: Any = response.json()
            if not isinstance(value, list):
                raise ValueError("Presidio response must be a result list")
            for result in value:
                if not isinstance(result, dict):
                    raise ValueError("Presidio result must be an object")
                if not isinstance(result.get("entity_type"), str):
                    raise ValueError("Presidio result is missing entity_type")
            return Sensitivity.PHI if value else Sensitivity.NONE
        except Exception:  # noqa: BLE001 - detector failures follow configured containment mode
            if self.fail_closed:
                log.exception("Presidio analysis failed; classifying content as sensitive")
                return Sensitivity.PHI
            log.exception("Presidio analysis failed; continuing with built-in detectors")
            return Sensitivity.NONE

    async def close(self) -> None:
        """Compatibility with application resource cleanup."""
