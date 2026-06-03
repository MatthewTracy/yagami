from __future__ import annotations

import json
import logging
from typing import Protocol

import httpx

from ..config import OllamaConfig
from .schema import Classification, Complexity, Intent, Sensitivity

log = logging.getLogger("yagami.classifier")


_SYSTEM_PROMPT = """You are a routing classifier. Read the user's most recent message and emit ONLY one strict JSON object matching this exact shape:

{"intent":"simple_qa|complex_reasoning|code|creative|image","sensitivity":"none|phi|phi_medical|secret","complexity":"low|medium|high","needs_tools":true|false,"needs_recall":true|false}

needs_tools: true ONLY when answering requires CALCULATION (arithmetic the model would otherwise approximate, like "14!" or "sqrt(2)*pi") OR a web FETCH of a specific URL/page the user named. Default false - never set true for opinion, summary, code, or "what does X mean" prompts.

needs_recall: true ONLY when the user is asking about something they told the assistant earlier - phrases like "what did we discuss", "what was my X", "remember when I said", "do you recall", "what's my favorite", "did I tell you about". Default false - never set true for general world knowledge questions.

Field meanings:

- intent.image  => user wants a generated picture/drawing/render/logo (a visual artifact). Pick image for "draw/paint/sketch/render/illustrate/generate a picture of X" and for "create/make/give me a (logo|poster|portrait|illustration|image|picture|drawing|design)". Do NOT pick image if the user wants a story, description, list, or explanation about a visual subject.
- intent.code  => question about code, debugging, error message, programming concept, or asking to write code.
- intent.complex_reasoning  => multi-step novel reasoning, design tradeoffs, deep technical synthesis, hard math. NOT triggered by long input alone.
- intent.creative => write a story, poem, song, fictional dialogue, essay.
- intent.simple_qa => everything else (factual lookups, summarization, rewrites, chat, casual Q&A).

- sensitivity.phi_medical => any clinical content (patient names, MRN, DOB, labs, meds, diagnoses, vitals, treatment plans).
- sensitivity.phi => personal health/identity info without clinical depth (SSN alone, home address, phone with personal context).
- sensitivity.secret => API keys (sk-..., ghp_..., AKIA..., JWTs), passwords, credentials.
- sensitivity.none => none of the above.

- complexity.high => genuinely hard task that needs careful multi-step reasoning. Long input alone does NOT mean high - summarizing a long email is low complexity.
- complexity.medium => moderate task.
- complexity.low => trivial or easy.

Examples:

User: what is 2+2
Output: {"intent":"simple_qa","sensitivity":"none","complexity":"low"}

User: Generate a unicorn for me
Output: {"intent":"image","sensitivity":"none","complexity":"low"}

User: draw a red sailboat at sunset
Output: {"intent":"image","sensitivity":"none","complexity":"low"}

User: write a python function that returns the nth fibonacci number
Output: {"intent":"code","sensitivity":"none","complexity":"low"}

User: Patient Jane Doe DOB 1962-04-12 has CHF and T2DM. Summarize.
Output: {"intent":"simple_qa","sensitivity":"phi_medical","complexity":"low"}

User: Diagnosis: stage II NSCLC. Plan a 6-cycle carbo/paclitaxel regimen.
Output: {"intent":"simple_qa","sensitivity":"phi_medical","complexity":"low"}

User: My SSN is 123-45-6789, what should I do about identity theft?
Output: {"intent":"simple_qa","sensitivity":"phi","complexity":"low"}

User: here is my key sk-NsqqVgaZIcLYxcdjvXdR0nHOQyn08RyUMasFjs93i3UfHuvd, please rotate it
Output: {"intent":"simple_qa","sensitivity":"secret","complexity":"low"}

User: Walk me through the trade-offs between optimistic and pessimistic concurrency control in distributed databases, with concrete examples and failure modes for each.
Output: {"intent":"complex_reasoning","sensitivity":"none","complexity":"high"}

User: Design a rate limiter that survives a 10x traffic spike without dropping legitimate requests; cover algorithm, data store, and failure modes.
Output: {"intent":"complex_reasoning","sensitivity":"none","complexity":"high"}

User: tell me about the picture of Dorian Gray
Output: {"intent":"simple_qa","sensitivity":"none","complexity":"low"}

User: write a short poem about the ocean
Output: {"intent":"creative","sensitivity":"none","complexity":"low"}

User: tell me a 2-paragraph story about a lost robot
Output: {"intent":"creative","sensitivity":"none","complexity":"low"}

User: Prove that the halting problem is undecidable. Cite the diagonalization argument.
Output: {"intent":"complex_reasoning","sensitivity":"none","complexity":"high"}

Now classify the next message. JSON only, no prose."""


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
                needs_tools=bool(parsed.get("needs_tools", False)),
                needs_recall=bool(parsed.get("needs_recall", False)),
            )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("classifier failed (%s); raising so policy falls back", exc)
            raise
