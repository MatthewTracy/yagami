from __future__ import annotations

import json
import logging
from typing import Protocol

import httpx

from ..config import OllamaConfig
from .schema import Classification, Complexity, Intent, Sensitivity

log = logging.getLogger("yagami.classifier")

_SYSTEM_PROMPT = (
    'Classify the user message. JSON only: {"intent":"simple_qa|complex_reasoning|code|creative|image",'
    '"sensitivity":"none|phi|phi_medical|secret","complexity":"low|medium|high"}. '
    "Choose 'image' when the user asks to generate/create/make/draw/paint/render/show/give/build "
    "a visual thing (animal, object, scene, character, logo, design) AND does not request text "
    "output (story, essay, poem, list, recipe, code, description). When ambiguous between image "
    "and creative writing, prefer image. "
    "phi/phi_medical for personal health, SSN, address, phone, medical details. "
    "secret for keys/passwords. high complexity for multi-step or specialized."
)


class ClassifierProtocol(Protocol):
    async def __call__(self, text: str) -> Classification: ...


class OllamaJSONClassifier:
    def __init__(self, config: OllamaConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(base_url=config.url, timeout=httpx.Timeout(30.0))

    async def __call__(self, text: str) -> Classification:
        body = {
            "model": self._config.classifier_model,
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.0},
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text[:4000]},
            ],
        }
        try:
            r = await self._client.post("/api/chat", json=body)
            r.raise_for_status()
            content = r.json()["message"]["content"]
            parsed = json.loads(content)
            return Classification(
                intent=Intent(parsed.get("intent", "simple_qa")),
                sensitivity=Sensitivity(parsed.get("sensitivity", "none")),
                complexity=Complexity(parsed.get("complexity", "low")),
            )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("classifier failed (%s); raising so policy falls back", exc)
            raise
