from __future__ import annotations

from yagami.backends.base import Message
from yagami.backends.ollama import _build_wire_messages


def test_no_system_prompt_passes_through():
    msgs = [Message(role="user", content="hi"), Message(role="assistant", content="hello")]
    wire = _build_wire_messages(msgs, None)
    assert wire == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_system_prompt_strips_existing_and_prepends():
    msgs = [
        Message(role="system", content="OLD SYSTEM 1"),
        Message(role="user", content="hi"),
        Message(role="system", content="OLD SYSTEM 2"),
        Message(role="assistant", content="hello"),
    ]
    wire = _build_wire_messages(msgs, "NEW SYSTEM")
    system_msgs = [m for m in wire if m["role"] == "system"]
    assert len(system_msgs) == 1, f"expected 1 system message, got {len(system_msgs)}: {system_msgs}"
    assert system_msgs[0]["content"] == "NEW SYSTEM"
    assert wire[0] == {"role": "system", "content": "NEW SYSTEM"}
    user_assistant_only = [m for m in wire if m["role"] != "system"]
    assert user_assistant_only == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_system_prompt_with_empty_history():
    wire = _build_wire_messages([], "ONLY SYSTEM")
    assert wire == [{"role": "system", "content": "ONLY SYSTEM"}]
