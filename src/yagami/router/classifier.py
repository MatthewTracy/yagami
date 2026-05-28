from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable, Protocol

import httpx

from ..backends.base import Message
from ..config import OllamaConfig
from .schema import Classification, Complexity, Intent, Sensitivity

log = logging.getLogger("yagami.classifier")

_SYSTEM_PROMPT = (
    "You classify the user's most recent message for routing. "
    "Respond ONLY with strict JSON matching this shape: "
    '{"intent": "simple_qa|complex_reasoning|code|creative|image", '
    '"sensitivity": "none|phi|phi_medical|secret", '
    '"complexity": "low|medium|high"}. '
    "Use phi or phi_medical if the message contains personal health info, SSNs, addresses, "
    "phone numbers, or medical details about a person. Use secret for API keys, passwords, "
    "or other credentials. Otherwise none. Choose high complexity for multi-step reasoning, "
    "long content, or specialized expertise. Do not include any prose."
)


class ClassifierProtocol(Protocol):
    async def __call__(self, history: list[Message]) -> Classification: ...


class OllamaJSONClassifier:
    def __init__(self, config: OllamaConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(base_url=config.url, timeout=httpx.Timeout(30.0))

    async def __call__(self, history: list[Message]) -> Classification:
        last = next((m.content for m in reversed(history) if m.role == "user"), "")
        body = {
            "model": self._config.classifier_model,
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.0},
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": last[:4000]},
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
