"""Skill protocol — tools the LLM can call mid-turn.

Parallels Backend. A Skill describes:
- name (e.g. "calc.eval")
- description (shown to the LLM in the tool list)
- input_schema (JSON Schema; what args the LLM must produce)
- requires_network (boolean — Skills that hit the network can be gated)
- sensitivity_ceiling (Sensitivity — if session sensitivity > this, skill refuses)
- async run(args, ctx) -> SkillResult — MUST NOT raise; surface errors via SkillResult.error
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..router.schema import Sensitivity


@dataclass
class SkillContext:
    """Runtime context handed to every skill invocation."""

    session_id: str
    session_sensitivity: Sensitivity = Sensitivity.NONE


@dataclass
class SkillResult:
    """Always returned — even on error. Skills must NOT raise."""

    ok: bool
    content: str = ""  # the value passed back to the LLM as the tool result
    error: str | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)  # UI hints, e.g. links, images


@runtime_checkable
class Skill(Protocol):
    name: str
    description: str
    input_schema: dict  # JSON Schema
    requires_network: bool
    sensitivity_ceiling: Sensitivity

    async def run(self, args: dict, ctx: SkillContext) -> SkillResult: ...
