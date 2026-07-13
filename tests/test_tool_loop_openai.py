"""OpenAI-format tool loop (tool_loop._run_openai), driven through the
public tool_loop.run() dispatcher with a scripted fake client - mirrors
test_tool_loop.py's fake-Anthropic pattern.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from yagami.backends.base import BackendOptions, Capability, Message
from yagami.backends.groq import GroqBackend
from yagami.config import YagamiConfig
from yagami.router import tool_loop
from yagami.router.schema import Sensitivity
from yagami.skills.adapters import openai_name_map, sanitize_openai_name, to_openai_tools
from yagami.skills.base import SkillContext, SkillResult


class EchoSkill:
    name = "test.echo"  # dotted on purpose - exercises sanitization
    description = "Echo the input string back."
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    requires_network = False
    sensitivity_ceiling = Sensitivity.PHI_MEDICAL

    async def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        return SkillResult(ok=True, content=f"echoed: {args.get('text', '')}")


# ---- Fake OpenAI chat.completions client ----


@dataclass
class _Fn:
    name: str
    arguments: str


@dataclass
class _ToolCall:
    id: str
    function: _Fn


@dataclass
class _Msg:
    content: str | None = None
    tool_calls: list[_ToolCall] = field(default_factory=list)


@dataclass
class _Choice:
    message: _Msg


@dataclass
class _Resp:
    choices: list[_Choice]


class _ScriptedOpenAI:
    def __init__(self, responses: list[_Resp]):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.chat = self
        self.completions = self

    async def create(self, **kwargs: Any) -> _Resp:
        self.calls.append(kwargs)
        if not self._responses:
            raise RuntimeError("scripted client ran out of responses")
        return self._responses.pop(0)


def _make_backend(client) -> GroqBackend:
    b = GroqBackend(YagamiConfig(), api_key="sk-groq-test")
    b._client = client  # type: ignore[assignment]
    return b


async def _collect(backend, skills: dict) -> list[dict]:
    chunks = []
    async for c in tool_loop.run(
        backend,
        [Message(role="user", content="do the thing")],
        BackendOptions(),
        session_id="s1",
        skills=skills,
    ):
        chunks.append(c)
    return chunks


# ---- adapters: sanitization ----


def test_sanitize_replaces_dots():
    assert sanitize_openai_name("mcp.server.tool") == "mcp__server__tool"
    assert sanitize_openai_name("calc.eval") == "calc__eval"


def test_openai_tools_are_sanitized_and_mapped_back():
    skills = [EchoSkill()]
    tools = to_openai_tools(skills)
    assert tools[0]["function"]["name"] == "test__echo"
    assert openai_name_map(skills) == {"test__echo": "test.echo"}


# ---- loop behavior ----


@pytest.mark.asyncio
async def test_plain_text_yields_text_then_done():
    client = _ScriptedOpenAI([_Resp(choices=[_Choice(_Msg(content="hi there"))])])
    backend = _make_backend(client)
    chunks = await _collect(backend, {"test.echo": EchoSkill()})
    assert [c["type"] for c in chunks] == ["text", "done"]
    assert chunks[0]["content"] == "hi there"
    # The request carried sanitized tool definitions.
    assert client.calls[0]["tools"][0]["function"]["name"] == "test__echo"


@pytest.mark.asyncio
async def test_tool_call_round_trip_resolves_sanitized_name():
    client = _ScriptedOpenAI(
        [
            _Resp(
                choices=[
                    _Choice(
                        _Msg(
                            tool_calls=[
                                _ToolCall(
                                    id="call_1",
                                    function=_Fn(
                                        name="test__echo",
                                        arguments=json.dumps({"text": "abc"}),
                                    ),
                                )
                            ]
                        )
                    )
                ]
            ),
            _Resp(choices=[_Choice(_Msg(content="final answer"))]),
        ]
    )
    backend = _make_backend(client)
    chunks = await _collect(backend, {"test.echo": EchoSkill()})

    tool_chunks = [c for c in chunks if c["type"] == "tool_call"]
    assert len(tool_chunks) == 1
    assert tool_chunks[0]["meta"]["name"] == "test.echo"  # REAL name in the UI chunk
    assert tool_chunks[0]["meta"]["ok"] is True
    assert tool_chunks[0]["meta"]["result"] == "echoed: abc"

    # The tool result went back to the model with the matching call id.
    second_call_messages = client.calls[1]["messages"]
    tool_msgs = [m for m in second_call_messages if m.get("role") == "tool"]
    assert tool_msgs[0]["tool_call_id"] == "call_1"
    assert tool_msgs[0]["content"] == "echoed: abc"

    assert chunks[-1]["type"] == "done"
    assert any(c["type"] == "text" and c["content"] == "final answer" for c in chunks)


@pytest.mark.asyncio
async def test_unknown_tool_surfaces_error_result():
    client = _ScriptedOpenAI(
        [
            _Resp(
                choices=[
                    _Choice(
                        _Msg(
                            tool_calls=[
                                _ToolCall(
                                    id="c1", function=_Fn(name="nope__missing", arguments="{}")
                                )
                            ]
                        )
                    )
                ]
            ),
            _Resp(choices=[_Choice(_Msg(content="ok"))]),
        ]
    )
    backend = _make_backend(client)
    chunks = await _collect(backend, {"test.echo": EchoSkill()})
    tool_chunks = [c for c in chunks if c["type"] == "tool_call"]
    assert tool_chunks[0]["meta"]["ok"] is False
    assert "unknown skill" in tool_chunks[0]["meta"]["error"]


@pytest.mark.asyncio
async def test_malformed_arguments_surface_error_not_crash():
    client = _ScriptedOpenAI(
        [
            _Resp(
                choices=[
                    _Choice(
                        _Msg(
                            tool_calls=[
                                _ToolCall(
                                    id="c1", function=_Fn(name="test__echo", arguments="{not json")
                                )
                            ]
                        )
                    )
                ]
            ),
            _Resp(choices=[_Choice(_Msg(content="ok"))]),
        ]
    )
    backend = _make_backend(client)
    chunks = await _collect(backend, {"test.echo": EchoSkill()})
    tool_chunks = [c for c in chunks if c["type"] == "tool_call"]
    assert tool_chunks[0]["meta"]["ok"] is False
    assert "malformed" in tool_chunks[0]["meta"]["error"]
    assert chunks[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_max_turns_bounded():
    endless = _Resp(
        choices=[
            _Choice(
                _Msg(
                    tool_calls=[
                        _ToolCall(
                            id="c",
                            function=_Fn(name="test__echo", arguments='{"text": "again"}'),
                        )
                    ]
                )
            )
        ]
    )
    client = _ScriptedOpenAI([endless] * (tool_loop.MAX_TURNS + 2))
    backend = _make_backend(client)
    chunks = await _collect(backend, {"test.echo": EchoSkill()})
    assert chunks[-2]["type"] == "error"
    assert "max turns" in chunks[-2]["content"]
    assert len(client.calls) == tool_loop.MAX_TURNS


@pytest.mark.asyncio
async def test_sensitivity_ceiling_enforced_same_as_anthropic_loop():
    """_run_skill is shared between both loops - a NONE-ceiling skill must
    refuse in a PHI session on the OpenAI path too."""

    class NetworkOnly:
        name = "test.network"
        description = "network skill"
        input_schema = {"type": "object", "properties": {}}
        requires_network = True
        sensitivity_ceiling = Sensitivity.NONE

        async def run(self, args, ctx):
            return SkillResult(ok=True, content="leaked!")

    client = _ScriptedOpenAI(
        [
            _Resp(
                choices=[
                    _Choice(
                        _Msg(
                            tool_calls=[
                                _ToolCall(
                                    id="c1", function=_Fn(name="test__network", arguments="{}")
                                )
                            ]
                        )
                    )
                ]
            ),
            _Resp(choices=[_Choice(_Msg(content="done"))]),
        ]
    )
    backend = _make_backend(client)
    chunks = []
    async for c in tool_loop.run(
        backend,
        [Message(role="user", content="patient context")],
        BackendOptions(),
        session_id="s1",
        session_sensitivity=Sensitivity.PHI_MEDICAL,
        skills={"test.network": NetworkOnly()},
    ):
        chunks.append(c)
    tool_chunks = [c for c in chunks if c["type"] == "tool_call"]
    assert tool_chunks[0]["meta"]["ok"] is False
    assert "exceeds ceiling" in tool_chunks[0]["meta"]["error"]


# ---- capability declarations ----


def test_compat_backends_declare_tools_capability():
    from yagami.backends.gemini import GeminiBackend
    from yagami.backends.mistral import MistralBackend
    from yagami.backends.openrouter import OpenRouterBackend

    for cls in (GroqBackend, MistralBackend, OpenRouterBackend, GeminiBackend):
        assert Capability.TOOLS in cls.capabilities, cls.__name__
