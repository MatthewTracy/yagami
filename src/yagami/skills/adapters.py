"""Convert Skill objects into the tool-list format each provider expects."""

from __future__ import annotations

from .base import Skill


def to_anthropic_tools(skills: list[Skill]) -> list[dict]:
    """Anthropic Messages API tool format."""
    return [
        {
            "name": s.name,
            "description": s.description,
            "input_schema": s.input_schema,
        }
        for s in skills
    ]


def to_openai_tools(skills: list[Skill]) -> list[dict]:
    """OpenAI Chat Completions tool format."""
    return [
        {
            "type": "function",
            "function": {
                "name": s.name,
                "description": s.description,
                "parameters": s.input_schema,
            },
        }
        for s in skills
    ]
