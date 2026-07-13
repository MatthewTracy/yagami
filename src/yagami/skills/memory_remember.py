"""memory.remember - let the model explicitly save something to
cross-session chat memory, instead of relying solely on the automatic
per-turn write gate in chat/stream.py. Completes half of the roadmap's
v0.5a item ("the LLM chooses when to remember").

Writes go through the exact same gate as automatic memory
(memory/store.queue_observation): SECRET is rejected outright, PHI rows
get the 7-day TTL, everything else 90 days. The observation is tagged with
the CURRENT session's sensitivity so a note saved during a PHI session
inherits PHI quarantine in retrieval.

sensitivity_ceiling=PHI_MEDICAL: saving locally is safe in a PHI session
(nothing leaves the device), but SECRET sessions rank above the ceiling
and are refused before the store's own rejection even runs.
"""

from __future__ import annotations

from ..memory.store import queue_observation
from ..router.schema import Sensitivity
from .base import Skill, SkillContext, SkillResult


class MemoryRemember:
    name = "memory.remember"
    description = (
        "Save a short note to the user's cross-session memory so future "
        "conversations can recall it. Use for durable facts the user "
        "states about themselves, their projects, or their preferences - "
        "not for transient chit-chat."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The fact to remember, one sentence or two."},
        },
        "required": ["text"],
    }
    requires_network = False  # local sqlite write + local embedding worker
    sensitivity_ceiling = Sensitivity.PHI_MEDICAL

    async def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        text = (args.get("text") or "").strip()
        if not text:
            return SkillResult(ok=False, error="missing 'text'")
        try:
            ids = await queue_observation(
                session_id=ctx.session_id,
                role="assistant",
                text=text,
                sensitivity=ctx.session_sensitivity,
                source_app="skill",
            )
        except Exception as exc:  # noqa: BLE001 - skills must never raise
            return SkillResult(ok=False, error=f"memory write failed: {exc}")
        if not ids:
            # The store's write gate said no (too short, or SECRET session).
            return SkillResult(
                ok=False,
                error="not saved: memory gate rejected it (too short, or secret-tagged session)",
            )
        return SkillResult(ok=True, content=f"Saved to memory ({len(ids)} chunk(s)).")


def build() -> Skill:
    return MemoryRemember()
