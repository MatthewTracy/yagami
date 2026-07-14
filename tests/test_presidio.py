from __future__ import annotations

import json

import httpx
import pytest

from yagami.backends.base import Capability, Message, Pricing
from yagami.config import RoutingConfig
from yagami.governance.presidio import PresidioInspector
from yagami.router.policy import OverrideRefused, RoutingPolicy
from yagami.router.schema import Sensitivity


class DummyBackend:
    capabilities = {Capability.TEXT}
    pricing = Pricing()

    def __init__(self, name: str, *, is_local: bool) -> None:
        self.name = name
        self.is_local = is_local


@pytest.mark.asyncio
async def test_presidio_detection_raises_sensitivity_without_retaining_text() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[{"entity_type": "PERSON", "start": 0, "end": 10, "score": 0.9}],
        )

    inspector = PresidioInspector(
        "http://127.0.0.1:5002",
        bearer_token="proxy-token",
        transport=httpx.MockTransport(handler),
    )

    sensitivity = await inspector.inspect("John Smith is a customer")

    assert sensitivity == Sensitivity.PHI
    assert requests[0].headers["Authorization"] == "Bearer proxy-token"
    payload = json.loads(requests[0].content)
    assert payload["language"] == "en"
    assert payload["return_decision_process"] is False


@pytest.mark.asyncio
async def test_presidio_failure_mode_is_explicit() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    closed = PresidioInspector("http://localhost:5002", transport=transport)
    open_inspector = PresidioInspector(
        "http://localhost:5002", transport=transport, fail_closed=False
    )

    assert await closed.inspect("hello") == Sensitivity.PHI
    assert await open_inspector.inspect("hello") == Sensitivity.NONE


def test_presidio_requires_explicit_secure_remote_configuration() -> None:
    with pytest.raises(ValueError, match="ALLOW_REMOTE"):
        PresidioInspector("https://presidio.example.test")
    with pytest.raises(ValueError, match="HTTPS"):
        PresidioInspector("http://presidio.example.test", allow_remote=True)


@pytest.mark.asyncio
async def test_external_inspector_prevents_current_context_cloud_route() -> None:
    class Inspector:
        async def inspect(self, text: str) -> Sensitivity:
            return Sensitivity.PHI if "John Smith" in text else Sensitivity.NONE

    local = DummyBackend("local", is_local=True)
    cloud = DummyBackend("cloud", is_local=False)
    policy = RoutingPolicy(
        config=RoutingConfig(default_backend="cloud"),
        backends={"local": local, "cloud": cloud},
        sensitivity_inspector=Inspector(),
    )

    decision = await policy.decide([Message(role="user", content="Summarize for John Smith")])

    assert decision.backend.name == "local"
    assert decision.classification["sensitivity"] == "phi"
    assert "external" in decision.classification["source"]


@pytest.mark.asyncio
async def test_external_inspector_prevents_inherited_context_cloud_route() -> None:
    class Inspector:
        async def inspect(self, text: str) -> Sensitivity:
            return Sensitivity.PHI if "John Smith" in text else Sensitivity.NONE

    local = DummyBackend("local", is_local=True)
    cloud = DummyBackend("cloud", is_local=False)
    policy = RoutingPolicy(
        config=RoutingConfig(default_backend="local"),
        backends={"local": local, "cloud": cloud},
        sensitivity_inspector=Inspector(),
    )

    with pytest.raises(OverrideRefused, match="history contains PHI"):
        await policy.decide(
            [
                Message(role="user", content="Record for John Smith"),
                Message(role="assistant", content="Done"),
                Message(role="user", content="Now summarize"),
            ],
            force_backend="cloud",
        )
