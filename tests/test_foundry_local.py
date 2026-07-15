from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from yagami.backends.foundry_local import FoundryLocalBackend, _service_urls
from yagami.config import FoundryLocalConfig


@pytest.mark.parametrize(
    "url",
    [
        "https://foundry.example.com/v1",
        "http://192.168.1.10:5272/v1",
        "http://localhost.evil.example:5272/v1",
        "http://localhost:5272/v1?token=secret",
        "http://localhost:0/v1",
        "http://localhost:not-a-port/v1",
    ],
)
def test_config_rejects_non_loopback_or_ambiguous_urls(url):
    with pytest.raises(ValidationError):
        FoundryLocalConfig(enabled=True, base_url=url)


def test_enabled_config_requires_endpoint():
    with pytest.raises(ValidationError):
        FoundryLocalConfig(enabled=True)


def test_enabled_config_requires_model():
    with pytest.raises(ValidationError):
        FoundryLocalConfig(enabled=True, base_url="http://localhost:5272/v1", model=" ")


def test_service_urls_accept_root_or_v1():
    assert _service_urls("http://[::1]:5272/v1/") == (
        "http://[::1]:5272/v1",
        "http://[::1]:5272",
    )


@pytest.mark.asyncio
async def test_health_uses_foundry_status_endpoint():
    requested: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return httpx.Response(200, json={"Endpoints": ["http://127.0.0.1:5272"]})

    backend = FoundryLocalBackend(
        FoundryLocalConfig(
            enabled=True,
            base_url="http://127.0.0.1:5272/v1",
            model="test-model",
        )
    )
    await backend._health_client.aclose()
    backend._health_client = httpx.AsyncClient(
        base_url="http://127.0.0.1:5272",
        transport=httpx.MockTransport(respond),
    )
    try:
        assert await backend.health() is True
        assert requested == ["/openai/status"]
    finally:
        await backend.close()
