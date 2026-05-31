from __future__ import annotations

from typing import AsyncIterator

import pytest

from yagami.backends.base import BackendChunk, BackendOptions, Capability, Message
from yagami.backends.retry import generate_with_retry


class FlakyBackend:
    name = "flaky"
    capabilities = {Capability.TEXT}
    is_local = False

    def __init__(self, fail_first_n: int, error_text: str = "503 service unavailable") -> None:
        self._fail_first_n = fail_first_n
        self._error_text = error_text
        self.calls = 0

    async def generate(
        self, messages: list[Message], *, options: BackendOptions
    ) -> AsyncIterator[BackendChunk]:
        self.calls += 1
        if self.calls <= self._fail_first_n:
            yield {"type": "error", "content": self._error_text, "meta": {}}
            yield {"type": "done", "content": "", "meta": {}}
            return
        yield {"type": "text", "content": "ok", "meta": {}}
        yield {"type": "done", "content": "", "meta": {}}

    async def health(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_recovers_after_transient_failure():
    b = FlakyBackend(fail_first_n=1)
    chunks = [
        c
        async for c in generate_with_retry(
            b, [Message(role="user", content="hi")], BackendOptions()
        )
    ]
    assert b.calls == 2
    types = [c["type"] for c in chunks]
    assert "text" in types
    assert types[-1] == "done"


@pytest.mark.asyncio
async def test_gives_up_after_max_attempts():
    b = FlakyBackend(fail_first_n=10)
    chunks = [
        c
        async for c in generate_with_retry(
            b, [Message(role="user", content="hi")], BackendOptions()
        )
    ]
    assert b.calls == 3
    last_two = [c["type"] for c in chunks[-2:]]
    assert last_two == ["error", "done"]
    assert "retries exhausted" in chunks[-2]["content"]


@pytest.mark.asyncio
async def test_non_transient_error_not_retried():
    b = FlakyBackend(fail_first_n=10, error_text="400 bad request")
    chunks = [
        c
        async for c in generate_with_retry(
            b, [Message(role="user", content="hi")], BackendOptions()
        )
    ]
    assert b.calls == 1
    assert chunks[-2]["type"] == "error"
    assert "400" in chunks[-2]["content"]


@pytest.mark.asyncio
async def test_no_retry_once_content_started():
    class HalfwayFail:
        name = "halfway"
        capabilities = {Capability.TEXT}
        is_local = False

        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, messages, *, options):
            self.calls += 1
            yield {"type": "text", "content": "part 1", "meta": {}}
            yield {"type": "error", "content": "503 then-died", "meta": {}}
            yield {"type": "done", "content": "", "meta": {}}

        async def health(self):
            return True

    b = HalfwayFail()
    chunks = [
        c
        async for c in generate_with_retry(
            b, [Message(role="user", content="hi")], BackendOptions()
        )
    ]
    assert b.calls == 1  # mid-stream errors are NOT retried
    assert any(c["type"] == "text" and c["content"] == "part 1" for c in chunks)
    assert any(c["type"] == "error" for c in chunks)
