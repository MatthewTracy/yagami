from __future__ import annotations

import pytest

from yagami.router.fast_path import can_bypass
from yagami.router.schema import Complexity, Intent, Sensitivity
from tests.test_phi_never_leaves import PHI_FIXTURES


SECRET_FIXTURES = [
    "Here's my key: sk-NsqqVgaZIcLYxcdjvXdR0nHOQyn08RyUMasFjs93i3UfHuvd",
    "GitHub token: ghp_abcdefghijklmnopqrstuvwxyz1234567890",
    "use this PAT github_pat_11ABCDEFG0xyz_token_value_here_pls",
    "AWS access: AKIAIOSFODNN7EXAMPLE",
    "temp creds ASIAIOSFODNN7EXAMPLE",
    "JWT: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NSJ9.SflKxwRJSMeKKF2QT4fwpMeJf36POk",
    "Anthropic: sk-ant-api03-abc123def456ghi789jkl012",
    "config: password=hunter2supersecret",
    "API_KEY: my_secret_value_long_enough",
]

CODE_FIXTURES = [
    "summarize this python function: def foo(x): return x*2",
    "what does `await` do in JavaScript",
    "```python\nprint('hi')\n```",
    "I got a stack trace, can you read it",
    "fix this bug for me",
    "why does this not work? import requests; r = requests.get(...)",
    "explain `console.log` in node",
    "running npm install hangs forever",
    "show me the cargo build output",
    "git rebase keeps failing",
    "function add(a, b) { return a + b; } what does this return",
    "class Foo extends Bar — what's the syntax",
]

IMAGE_FIXTURES = [
    "draw a red sailboat",
    "image of a forest",
    "picture of mars",
    "/image cat",
    "generate an image of a dog",
    "paint me a sunrise",
]


@pytest.mark.parametrize("prompt", PHI_FIXTURES)
def test_phi_never_bypasses(prompt: str):
    assert can_bypass(prompt) is None, f"PHI prompt slipped past bypass: {prompt[:60]}"


@pytest.mark.parametrize("prompt", SECRET_FIXTURES)
def test_secret_never_bypasses(prompt: str):
    assert can_bypass(prompt) is None, f"Secret prompt slipped past bypass: {prompt[:60]}"


@pytest.mark.parametrize("prompt", CODE_FIXTURES)
def test_code_never_bypasses(prompt: str):
    assert can_bypass(prompt) is None, f"Code prompt slipped past bypass: {prompt[:60]}"


@pytest.mark.parametrize("prompt", IMAGE_FIXTURES)
def test_image_keyword_never_bypasses(prompt: str):
    assert can_bypass(prompt) is None, f"Image prompt slipped past bypass: {prompt[:60]}"


@pytest.mark.parametrize(
    "prompt",
    [
        "hello",
        "hi there",
        "what is 2+2",
        "what's the capital of France",
        "thanks!",
        "tell me a joke",
        "lol",
        "good morning",
    ],
)
def test_simple_prompts_bypass(prompt: str):
    c = can_bypass(prompt)
    assert c is not None, f"Simple prompt should have bypassed: {prompt!r}"
    assert c.intent == Intent.SIMPLE_QA
    assert c.sensitivity == Sensitivity.NONE
    assert c.complexity == Complexity.LOW


def test_long_prompt_never_bypasses():
    assert can_bypass("hi " * 100) is None


def test_empty_prompt_never_bypasses():
    assert can_bypass("") is None
