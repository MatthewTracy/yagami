from __future__ import annotations

import pytest
from pydantic import ValidationError

from yagami.config import OllamaConfig


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://[::1]:11434",
        "http://host.docker.internal:11434",
    ],
)
def test_device_ollama_accepts_device_endpoints(url: str) -> None:
    assert OllamaConfig(url=url).trust_zone == "device"


@pytest.mark.parametrize(
    "url",
    [
        "http://ollama.internal:11434",
        "https://ollama.example.com",
        "http://10.20.30.40:11434",
    ],
)
def test_network_ollama_requires_explicit_trust_zone(url: str) -> None:
    with pytest.raises(ValidationError, match="private_network"):
        OllamaConfig(url=url)
    assert OllamaConfig(url=url, trust_zone="private_network").trust_zone == "private_network"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://localhost:11434",
        "http://user:pass@localhost:11434",
        "http://localhost:11434/api",
        "http://localhost:11434?token=secret",
    ],
)
def test_ollama_rejects_unsafe_service_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        OllamaConfig(url=url)


def test_assignment_revalidates_environment_override() -> None:
    config = OllamaConfig()
    with pytest.raises(ValidationError, match="private_network"):
        config.url = "http://ollama.internal:11434"
    config.trust_zone = "private_network"
    config.url = "http://ollama.internal:11434"
    assert config.url == "http://ollama.internal:11434"
