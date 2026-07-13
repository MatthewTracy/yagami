"""memory.recall - let the model search cross-session chat memory on
demand, instead of relying solely on the classifier's needs_recall signal.
The other half of the roadmap's v0.5a item. Distinct from kb.recall, which
searches the folder-indexed document corpus - memory.* is chat memory,
kb.* is documents.

Retrieval goes through the same Retriever the automatic needs_recall path
uses, including its PHI quarantine (_phi_safe_filter): PHI observations
never surface unless the CURRENT turn is itself PHI, and the current
session's own messages are excluded (they're already in context).

sensitivity_ceiling=NONE, same reasoning as kb.recall: the search itself
is local, but results flow into the conversation like any tool result and
ride along to whichever cloud backend is driving the tool-use turn.
"""

from __future__ import annotations

from ..config import get_config
from ..memory.embedder import Embedder
from ..memory.retriever import Retriever
from ..router.schema import Sensitivity
from .base import Skill, SkillContext, SkillResult


class MemoryRecall:
    name = "memory.recall"
    description = (
        "Search the user's cross-session chat memory (things said in past "
        "conversations) for entries relevant to a query. Returns up to 5 "
        "matching notes. For searching indexed documents use kb.recall "
        "instead."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search past conversations for."},
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
        retriever = Retriever(Embedder(url=cfg.ollama.url, model=cfg.memory.embedding_model))
        try:
            hits = await retriever.fetch(
                query,
                k=5,
                exclude_session=ctx.session_id,
                current_sens=ctx.session_sensitivity,
            )
        except Exception as exc:  # noqa: BLE001 - skills must never raise
            return SkillResult(ok=False, error=f"memory search failed: {exc}")
        if not hits:
            return SkillResult(ok=True, content="No matching memories found.")
        lines = [f"[{h.role} in session {h.session_id[:8]}] {h.text}" for h in hits]
        return SkillResult(ok=True, content="\n\n".join(lines))


def build() -> Skill:
    return MemoryRecall()
