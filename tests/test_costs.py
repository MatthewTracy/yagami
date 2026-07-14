from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from yagami.api import costs as costs_api
from yagami.backends.anthropic import ClaudeBackend
from yagami.backends.echo import EchoBackend
from yagami.backends.ollama import OllamaBackend
from yagami.backends.stability import StabilityImageBackend
from yagami.config import (
    AnthropicConfig,
    OllamaConfig,
    ProfileOverrides,
    StabilityConfig,
    YagamiConfig,
)
from yagami.telemetry.costs import _local_day_start_ms, estimate_cost, rough_token_count


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


def test_local_day_start_uses_local_calendar_midnight():
    tz = timezone(timedelta(hours=-5))
    now = datetime(2026, 7, 13, 15, 42, 10, tzinfo=tz)
    expected = datetime(2026, 7, 13, 0, 0, 0, tzinfo=tz)
    assert _local_day_start_ms(now) == int(expected.timestamp() * 1000)


@pytest.mark.asyncio
async def test_cost_api_uses_active_profile_cap(monkeypatch):
    cfg = YagamiConfig()
    cfg.routing.daily_spend_cap_usd = 10
    cfg.routing.active_profile = "work"
    cfg.profiles["work"] = ProfileOverrides(daily_spend_cap_usd=2)

    async def today() -> float:
        return 1.25

    async def session(_session_id: str) -> float:
        return 0.5

    monkeypatch.setattr(costs_api, "get_config", lambda: cfg)
    monkeypatch.setattr(costs_api, "spend_today_usd", today)
    monkeypatch.setattr(costs_api, "spend_session_usd", session)

    result = await costs_api.costs("s1")

    assert result["daily_cap_usd"] == 2
    assert result["cap_remaining_usd"] == 0.75
