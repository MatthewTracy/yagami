from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

from yagami.backends.base import Backend, BackendChunk, BackendOptions, Capability, Message
from yagami.config import RoutingConfig
from yagami.router.policy import RoutingPolicy
from yagami.router.schema import Classification, Complexity, Intent, Sensitivity
from yagami.storage.db import close_db, open_db


class FakeBackend:
    def __init__(self, name: str, *, is_local: bool, capabilities: set[Capability] | None = None) -> None:
        self.name = name
        self.is_local = is_local
        self.capabilities = capabilities or {Capability.TEXT}
        self.calls: list[list[Message]] = []

    async def generate(
        self, messages: list[Message], *, options: BackendOptions
    ) -> AsyncIterator[BackendChunk]:
        self.calls.append(list(messages))
        yield {"type": "text", "content": f"{self.name}-reply", "meta": {}}
        yield {"type": "done", "content": "", "meta": {}}

    async def health(self) -> bool:
        return True


@pytest.fixture
def backends() -> dict[str, Backend]:
    return {
        "ollama": FakeBackend("ollama", is_local=True, capabilities={Capability.TEXT, Capability.CODE}),
        "anthropic": FakeBackend("anthropic", is_local=False, capabilities={Capability.TEXT, Capability.LONG_CONTEXT}),
        "stability": FakeBackend("stability", is_local=False, capabilities={Capability.IMAGE}),
    }


def fixed_classifier(cls: Classification):
    async def _classify(_history):
        return cls
    return _classify


@pytest.fixture
def make_policy(backends):
    def _make(classification: Classification | None = None, *, routing: RoutingConfig | None = None) -> RoutingPolicy:
        return RoutingPolicy(
            config=routing or RoutingConfig(),
            backends=backends,
            classifier=fixed_classifier(classification) if classification else None,
        )
    return _make


@pytest.fixture
def user_msg():
    def _make(text: str) -> list[Message]:
        return [Message(role="user", content=text)]
    return _make


@pytest_asyncio.fixture
async def fresh_db(tmp_path: Path):
    db_file = tmp_path / "yagami_test.db"
    conn = await open_db(db_file)
    yield conn
    await close_db()
