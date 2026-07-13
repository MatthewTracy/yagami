"""kb.recall - search the folder-indexed document knowledge base
(memory/documents.py). Distinct from cross-session chat memory: this is
reference material the user explicitly indexed via `POST /api/kb/index`,
not something the classifier tagged from a conversation.

sensitivity_ceiling=NONE (same as web.fetch, and stricter than calc.eval):
the search itself never leaves the device, but its RESULTS get appended to
the conversation and - like any tool result - flow to whichever backend is
driving the current tool-use turn (Anthropic-only today, see
router/tool_loop.py). The classifier only ever sees the user's typed
message, not what's inside indexed documents, so this skill refuses
whenever the *current turn* is flagged sensitive at all, as a floor -
don't index folders containing content you wouldn't want sent to whatever
cloud backend your tool-use turns use.
"""

from __future__ import annotations

from ..config import get_config
from ..memory.documents import search as documents_search
from ..memory.embedder import Embedder
from ..router.schema import Sensitivity
from .base import Skill, SkillContext, SkillResult


class KbRecall:
    name = "kb.recall"
    description = (
        "Search the user's indexed document knowledge base (folders they "
        "chose to index via POST /api/kb/index) for passages relevant to a "
        "query. Returns up to 5 matching chunks, each tagged with its "
        "source file."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
        },
        "required": ["query"],
    }
    requires_network = False  # local DB + local Ollama embeddings only
    sensitivity_ceiling = Sensitivity.NONE

    async def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        query = (args.get("query") or "").strip()
        if not query:
            return SkillResult(ok=False, error="missing 'query'")
        cfg = get_config()
        embedder = Embedder(url=cfg.ollama.url, model=cfg.memory.embedding_model)
        hits = await documents_search(query, embedder=embedder)
        if not hits:
            return SkillResult(ok=True, content="No matching documents found.")
        lines = [f"[{h['source_path']}#chunk{h['chunk_index']}] {h['text']}" for h in hits]
        return SkillResult(
            ok=True,
            content="\n\n".join(lines),
            artifacts={"sources": sorted({h["source_path"] for h in hits})},
        )


def build() -> Skill:
    return KbRecall()
