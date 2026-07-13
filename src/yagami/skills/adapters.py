"""Convert Skill objects into the tool-list format each provider expects."""

from __future__ import annotations

from .base import Skill


def to_anthropic_tools(skills: list[Skill]) -> list[dict]:
    """Anthropic Messages API tool format. Dotted skill names (calc.eval)
    are accepted as-is - no sanitization needed."""
    return [
        {
            "name": s.name,
            "description": s.description,
            "input_schema": s.input_schema,
        }
        for s in skills
    ]


def sanitize_openai_name(name: str) -> str:
    """OpenAI function names must match ^[a-zA-Z0-9_-]+$ - dots are not
    allowed, but every Yagami skill uses them (calc.eval, kb.recall,
    mcp.<server>.<tool>). Map '.' to '__'; the tool loop keeps a reverse map
    (openai_name_map) to resolve tool_calls back to real skill names."""
    return name.replace(".", "__")


def openai_name_map(skills: list[Skill]) -> dict[str, str]:
    """sanitized name -> real skill name, for resolving tool_calls."""
    return {sanitize_openai_name(s.name): s.name for s in skills}


def to_openai_tools(skills: list[Skill]) -> list[dict]:
    """OpenAI Chat Completions tool format, with sanitized names."""
    return [
        {
            "type": "function",
            "function": {
                "name": sanitize_openai_name(s.name),
                "description": s.description,
                "parameters": s.input_schema,
            },
        }
        for s in skills
    ]
