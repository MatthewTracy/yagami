from __future__ import annotations

from yagami.backends.anthropic import ClaudeBackend
from yagami.backends.echo import EchoBackend
from yagami.backends.ollama import OllamaBackend
from yagami.backends.stability import StabilityImageBackend
from yagami.config import AnthropicConfig, OllamaConfig, StabilityConfig
from yagami.telemetry.costs import estimate_cost, rough_token_count


def test_ollama_is_free():
    b = OllamaBackend(OllamaConfig())
    assert estimate_cost(b, tokens_in=10_000, tokens_out=5_000) == 0.0


def test_echo_is_free():
    b = EchoBackend()
    assert estimate_cost(b, tokens_in=1_000, tokens_out=1_000) == 0.0


def test_anthropic_text_pricing():
    # 1M in + 1M out at $3 + $15 = $18
    b = ClaudeBackend(AnthropicConfig(), api_key="sk-ant-test")
    cost = estimate_cost(b, tokens_in=1_000_000, tokens_out=1_000_000)
    assert abs(cost - 18.0) < 1e-9


def test_stability_per_image():
    b = StabilityImageBackend(StabilityConfig(), api_key="sk-stab-test")
    assert estimate_cost(b, images=3) == 0.03 * 3


def test_unknown_backend_is_zero():
    """`estimate_cost(None, ...)` returns 0 for the legacy "I don't know the
    backend" path; the registry guarantees it's never called with an unknown
    name in production."""
    assert estimate_cost(None, tokens_in=100, tokens_out=100) == 0.0


def test_rough_token_count_4_char_rule():
    assert rough_token_count("") == 0
    assert rough_token_count("hi") == 1
    assert rough_token_count("hello world!") == 3
    assert rough_token_count("a" * 4000) == 1000
