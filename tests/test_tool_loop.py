from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from yagami.backends.anthropic import ClaudeBackend
from yagami.backends.base import BackendOptions, Message
from yagami.config import AnthropicConfig
from yagami.router import tool_loop
from yagami.router.schema import Sensitivity
from yagami.skills.base import SkillContext, SkillResult


# ---- Test skills with deterministic behavior ----


class EchoSkill:
    name = "test.echo"
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


class NetworkOnly:
    name = "test.network"
    description = "Pretends to need the network."
    input_schema = {"type": "object", "properties": {}}
    requires_network = True
    sensitivity_ceiling = Sensitivity.NONE  # refused in any PHI session

    async def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        return SkillResult(ok=True, content="should-not-reach-here-in-phi-session")


class Exploder:
    name = "test.explode"
    description = "Raises an exception."
    input_schema = {"type": "object", "properties": {}}
    requires_network = False
    sensitivity_ceiling = Sensitivity.PHI_MEDICAL

    async def run(self, args: dict, ctx: SkillContext) -> SkillResult:
        raise RuntimeError("boom")


# ---- Fake Anthropic client ----


@dataclass
class _Block:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict | None = None


@dataclass
class _Resp:
    content: list[_Block]


class _ScriptedClient:
    """Drives tool_loop by yielding pre-scripted Messages.create responses."""

    def __init__(self, responses: list[_Resp]):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = self  # so client.messages.create works

    async def create(self, **kwargs: Any) -> _Resp:
        self.calls.append(kwargs)
        if not self._responses:
            raise RuntimeError("scripted client ran out of responses")
        return self._responses.pop(0)


def _make_backend(client) -> ClaudeBackend:
    b = ClaudeBackend(AnthropicConfig(), api_key="sk-ant-test")
    b._client = client  # type: ignore[assignment]
    return b


# ---- Tests ----


@pytest.mark.asyncio
async def test_plain_text_response_yields_text_then_done():
    client = _ScriptedClient([_Resp(content=[_Block(type="text", text="hello world")])])
    backend = _make_backend(client)
    chunks = [
        c
        async for c in tool_loop.run(
            backend,
            [Message(role="user", content="hi")],
            BackendOptions(),
            session_id="s1",
            skills={"test.echo": EchoSkill()},
        )
    ]
    types = [c["type"] for c in chunks]
    assert types == ["text", "done"]
    assert chunks[0]["content"] == "hello world"


@pytest.mark.asyncio
async def test_one_tool_call_then_final_text():
    client = _ScriptedClient(
        [
            _Resp(
                content=[
                    _Block(
                        type="tool_use",
                        id="tool_1",
                        name="test.echo",
                        input={"text": "hi"},
                    )
                ]
            ),
            _Resp(content=[_Block(type="text", text="done: echoed: hi")]),
        ]
    )
    backend = _make_backend(client)
    chunks = [
        c
        async for c in tool_loop.run(
            backend,
            [Message(role="user", content="please echo hi")],
            BackendOptions(),
            session_id="s1",
            skills={"test.echo": EchoSkill()},
        )
    ]
    types = [c["type"] for c in chunks]
    assert types == ["tool_call", "text", "done"]
    assert chunks[0]["meta"]["name"] == "test.echo"
    assert chunks[0]["meta"]["ok"] is True
    assert chunks[0]["meta"]["result"] == "echoed: hi"
    assert chunks[1]["content"] == "done: echoed: hi"
    # Two SDK calls - initial + post-tool-result.
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_unknown_skill_returns_error_to_model():
    client = _ScriptedClient(
        [
            _Resp(
                content=[
                    _Block(
                        type="tool_use",
                        id="t1",
                        name="totally.fake",
                        input={},
                    )
                ]
            ),
            _Resp(content=[_Block(type="text", text="ok, moving on")]),
        ]
    )
    backend = _make_backend(client)
    chunks = [
        c
        async for c in tool_loop.run(
            backend,
            [Message(role="user", content="x")],
            BackendOptions(),
            session_id="s1",
            skills={"test.echo": EchoSkill()},
        )
    ]
    # tool_call surfaces ok=False; loop continues.
    tool_chunk = next(c for c in chunks if c["type"] == "tool_call")
    assert tool_chunk["meta"]["ok"] is False
    assert "unknown" in tool_chunk["meta"]["error"]
    assert chunks[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_sensitivity_ceiling_enforced():
    """Skill with sensitivity_ceiling=NONE refused in any PHI session."""
    client = _ScriptedClient(
        [
            _Resp(
                content=[
                    _Block(
                        type="tool_use",
                        id="t1",
                        name="test.network",
                        input={},
                    )
                ]
            ),
            _Resp(content=[_Block(type="text", text="couldn't fetch")]),
        ]
    )
    backend = _make_backend(client)
    chunks = [
        c
        async for c in tool_loop.run(
            backend,
            [Message(role="user", content="x")],
            BackendOptions(),
            session_id="s1",
            session_sensitivity=Sensitivity.PHI_MEDICAL,
            skills={"test.network": NetworkOnly()},
        )
    ]
    tool_chunk = next(c for c in chunks if c["type"] == "tool_call")
    assert tool_chunk["meta"]["ok"] is False
    assert "refused" in tool_chunk["meta"]["error"]


@pytest.mark.asyncio
async def test_skill_exception_does_not_crash_loop():
    client = _ScriptedClient(
        [
            _Resp(
                content=[
                    _Block(
                        type="tool_use",
                        id="t1",
                        name="test.explode",
                        input={},
                    )
                ]
            ),
            _Resp(content=[_Block(type="text", text="moved on")]),
        ]
    )
    backend = _make_backend(client)
    chunks = [
        c
        async for c in tool_loop.run(
            backend,
            [Message(role="user", content="x")],
            BackendOptions(),
            session_id="s1",
            skills={"test.explode": Exploder()},
        )
    ]
    tool_chunk = next(c for c in chunks if c["type"] == "tool_call")
    assert tool_chunk["meta"]["ok"] is False
    assert "unexpected" in tool_chunk["meta"]["error"]
    assert chunks[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_max_turns_yields_error_done():
    # Force a loop that never stops asking for tools.
    resp = _Resp(
        content=[_Block(type="tool_use", id="t1", name="test.echo", input={"text": "loop"})]
    )
    client = _ScriptedClient([resp] * (tool_loop.MAX_TURNS + 5))
    backend = _make_backend(client)
    chunks = [
        c
        async for c in tool_loop.run(
            backend,
            [Message(role="user", content="x")],
            BackendOptions(),
            session_id="s1",
            skills={"test.echo": EchoSkill()},
        )
    ]
    # Loop hit the cap then emitted an error + done.
    assert chunks[-1]["type"] == "done"
    assert any(c["type"] == "error" and "max turns" in c["content"] for c in chunks)


@pytest.mark.asyncio
async def test_no_skills_falls_back_to_plain_generate(monkeypatch):
    """If no skills are registered, tool_loop degrades to backend.generate."""
    calls = {"plain": 0}

    async def fake_generate(self, messages, *, options):
        calls["plain"] += 1
        yield {"type": "text", "content": "no-tools-path", "meta": {}}
        yield {"type": "done", "content": "", "meta": {}}

    monkeypatch.setattr(ClaudeBackend, "generate", fake_generate)
    backend = _make_backend(_ScriptedClient([]))  # client unused on this path
    chunks = [
        c
        async for c in tool_loop.run(
            backend,
            [Message(role="user", content="x")],
            BackendOptions(),
            session_id="s1",
            skills={},
        )
    ]
    assert calls["plain"] == 1
    assert chunks[0]["content"] == "no-tools-path"
