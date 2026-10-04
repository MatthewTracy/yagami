"""Adversarial inputs and generated arithmetic without real outbound requests."""

from __future__ import annotations

import asyncio
import math
import random

import httpx
import pytest

from yagami.skills.base import SkillContext
from yagami.skills.calc_eval import CalcEval
from yagami.skills.web_fetch import WebFetch


def _context() -> SkillContext:
    return SkillContext(session_id="edge-cases")


def _arithmetic(seed: int) -> tuple[str, int]:
    rng = random.Random(seed)  # noqa: S311 -- reproducible arithmetic fixtures, not credentials

    def expression(depth: int) -> tuple[str, int]:
        if depth == 0:
            value = rng.randint(-20, 20)
            return str(value), value
        left, a = expression(depth - 1)
        right, b = expression(depth - 1)
        operation = rng.choice(["+", "-", "*", "//", "%"])
        if operation in {"//", "%"}:
            right, b = str(abs(b) + 1), abs(b) + 1
        values = {
            "+": lambda: a + b,
            "-": lambda: a - b,
            "*": lambda: a * b,
            "//": lambda: a // b,
            "%": lambda: a % b,
        }
        return f"({left} {operation} {right})", values[operation]()

    return expression(4)


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", range(80))
async def test_generated_arithmetic_matches_independent_results(seed):
    expression, expected = _arithmetic(seed)
    result = await CalcEval().run({"expression": expression}, _context())
    assert result.ok, (expression, result.error)
    assert result.content == str(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expression",
    [
        "True",
        "False",
        "None",
        "'hello'",
        "[1, 2]",
        "{'x': 1}",
        "(x for x in [1])",
        "lambda: 1",
        "sqrt(x=4)",
        "1 << 4000",
        "2 ^ 3",
        "().__class__",
        "(-1)**0.5",
        "factorial(-1)",
        "log(0)",
        "sqrt(-1)",
        "pow(2, 3, 4, 5)",
        "min()",
        "9 % 0",
        "9 // 0",
        "round()",
        "factorial(513)",
        "2**4096",
        hex(1 << 4096),
    ],
)
async def test_calculator_rejects_invalid_or_excessive_expressions(expression):
    result = await CalcEval().run({"expression": expression}, _context())
    assert not result.ok
    assert result.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("round(pi, 3)", "3.142"),
        ("abs(-10)", "10"),
        ("floor(-1.5)", "-2"),
        ("ceil(-1.5)", "-1"),
        ("min(3, -4, 2)", "-4"),
        ("max(3, -4, 2)", "3"),
        ("factorial(0)", "1"),
        ("2**0", "1"),
        ("pow(3, -1, 7)", "5"),
    ],
)
async def test_calculator_numeric_corner_cases(expression, expected):
    result = await CalcEval().run({"expression": expression}, _context())
    assert result.ok
    assert result.content == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://trusted.example:invalid/page",
        "https://trusted.example:65536/",
        "https://trusted.example:-1/",
        "https://[not-an-ip]/",
        "https://username:password@trusted.example/",
        "https://trusted.example/path\nsecret",
        "https://trusted.example/path\x00secret",
        "https://trusted.example.attacker.test/",
        "https://trusted.example@attacker.test/",
        "file:///etc/passwd",
        "//trusted.example/",
    ],
)
async def test_fetch_refuses_bad_urls_without_contacting_transport(url):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text="unexpected request")

    result = await WebFetch(
        allowlist={"trusted.example"}, transport=httpx.MockTransport(handler)
    ).run({"url": url}, _context())
    assert not result.ok
    assert not requests


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_fetch_follows_relative_redirects_and_validates_each_hop(status):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(status, headers={"location": "/next"})
        return httpx.Response(200, text="trusted result")

    result = await WebFetch(
        allowlist={"trusted.example"}, transport=httpx.MockTransport(handler)
    ).run({"url": "https://trusted.example/start"}, _context())
    assert result.ok
    assert result.content == "trusted result"
    assert result.artifacts["redirects"] == 1
    assert requests == ["https://trusted.example/start", "https://trusted.example/next"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    [
        "http://trusted.example/",
        "https://trusted.example:bad/",
        "https://user:pass@trusted.example/",
        "https://attacker.test/",
    ],
)
async def test_redirect_cannot_bypass_url_checks(location):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": location})

    result = await WebFetch(
        allowlist={"trusted.example"}, transport=httpx.MockTransport(handler)
    ).run({"url": "https://trusted.example/"}, _context())
    assert not result.ok
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_redirect_cycles_are_bounded():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "/loop"})

    result = await WebFetch(
        allowlist={"trusted.example"}, transport=httpx.MockTransport(handler)
    ).run({"url": "https://trusted.example/loop"}, _context())
    assert not result.ok
    assert result.error == "too many redirects"
    assert len(requests) == 6


@pytest.mark.asyncio
async def test_fetch_preserves_plain_text_angle_brackets_and_line_breaks():
    body = "Use <token> as a placeholder.\n2 < 3 and 5 > 4."
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    result = await WebFetch(allowlist={"trusted.example"}, transport=transport).run(
        {"url": "https://trusted.example/text"}, _context()
    )
    assert result.ok
    assert result.content == body


@pytest.mark.asyncio
async def test_fetch_has_a_total_deadline_even_with_a_stream_that_never_ends(monkeypatch):
    import yagami.skills.web_fetch as fetch

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"start"
            await asyncio.Event().wait()

    monkeypatch.setattr(fetch, "_FETCH_TIMEOUT_SECONDS", 0.02)
    transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=SlowStream()))
    result = await asyncio.wait_for(
        WebFetch(allowlist={"trusted.example"}, transport=transport).run(
            {"url": "https://trusted.example/slow"}, _context()
        ),
        timeout=1,
    )
    assert not result.ok
    assert result.error == "fetch failed: TimeoutError"


@pytest.mark.asyncio
async def test_fetch_rejects_compression_before_reading_or_decompressing():
    class UnreadStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("compressed response body must not be read")
            yield b""

    def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=UnreadStream())

    result = await WebFetch(
        allowlist={"trusted.example"}, transport=httpx.MockTransport(handler)
    ).run({"url": "https://trusted.example/compressed"}, _context())
    assert not result.ok
    assert "compressed" in result.error


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [199_999, 200_000, 200_001])
async def test_fetch_byte_limit_handles_multibyte_utf8_at_the_boundary(size):
    body = ("é" * math.ceil(size / 2)).encode()[:size]
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, content=body, headers={"content-type": "text/plain; charset=utf-8"}
        )
    )
    result = await WebFetch(allowlist={"trusted.example"}, transport=transport).run(
        {"url": "https://trusted.example/unicode"}, _context()
    )
    assert result.ok
    assert result.artifacts["bytes"] == min(size, 200_000)
    assert result.content.endswith("... [truncated]") == (size > 200_000)
