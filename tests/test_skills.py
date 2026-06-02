from __future__ import annotations

import math

import pytest

from yagami.router.schema import Sensitivity
from yagami.skills.adapters import to_anthropic_tools, to_openai_tools
from yagami.skills.base import SkillContext, SkillResult
from yagami.skills.calc_eval import CalcEval
from yagami.skills.registry import discover_skills
from yagami.skills.web_fetch import WebFetch


def _ctx(sens: Sensitivity = Sensitivity.NONE) -> SkillContext:
    return SkillContext(session_id="s1", session_sensitivity=sens)


# ---- calc.eval ----


@pytest.mark.asyncio
async def test_calc_basic_arithmetic():
    res = await CalcEval().run({"expression": "2 + 3 * 4"}, _ctx())
    assert res.ok is True
    assert res.content == "14"


@pytest.mark.asyncio
async def test_calc_math_functions():
    res = await CalcEval().run({"expression": "sqrt(2) * pi"}, _ctx())
    assert res.ok is True
    assert abs(float(res.content) - math.sqrt(2) * math.pi) < 1e-9


@pytest.mark.asyncio
async def test_calc_factorial():
    res = await CalcEval().run({"expression": "factorial(14)"}, _ctx())
    assert res.ok is True
    assert res.content == str(math.factorial(14))


@pytest.mark.asyncio
async def test_calc_rejects_attribute_access():
    res = await CalcEval().run({"expression": "__import__('os').system('echo pwned')"}, _ctx())
    assert res.ok is False
    # AST walker refuses Call nodes whose func isn't in whitelist.
    assert res.error is not None
    assert "not allowed" in res.error


@pytest.mark.asyncio
async def test_calc_rejects_name_lookup():
    res = await CalcEval().run({"expression": "open"}, _ctx())
    assert res.ok is False
    assert "not allowed" in res.error


@pytest.mark.asyncio
async def test_calc_rejects_invalid_syntax():
    res = await CalcEval().run({"expression": "2 +"}, _ctx())
    assert res.ok is False


@pytest.mark.asyncio
async def test_calc_handles_div_by_zero():
    res = await CalcEval().run({"expression": "1/0"}, _ctx())
    assert res.ok is False
    assert "division" in res.error.lower()


# ---- web.fetch ----


@pytest.mark.asyncio
async def test_web_fetch_rejects_http():
    res = await WebFetch().run({"url": "http://example.com"}, _ctx())
    assert res.ok is False
    assert "https" in res.error.lower()


@pytest.mark.asyncio
async def test_web_fetch_rejects_non_allowlisted_host():
    res = await WebFetch().run({"url": "https://example.com/page"}, _ctx())
    assert res.ok is False
    assert "allowlist" in res.error.lower()


@pytest.mark.asyncio
async def test_web_fetch_missing_url():
    res = await WebFetch().run({}, _ctx())
    assert res.ok is False
    assert "url" in res.error.lower()


# ---- adapters ----


def test_adapters_emit_correct_shape():
    skills = [CalcEval(), WebFetch()]
    anth = to_anthropic_tools(skills)
    oai = to_openai_tools(skills)

    assert len(anth) == 2
    assert anth[0]["name"] == "calc.eval"
    assert "input_schema" in anth[0]

    assert len(oai) == 2
    assert oai[0]["type"] == "function"
    assert oai[0]["function"]["name"] == "calc.eval"
    assert "parameters" in oai[0]["function"]


# ---- registry ----


def test_registry_finds_first_party_skills():
    skills = discover_skills()
    assert "calc.eval" in skills
    assert "web.fetch" in skills
    # Helpers aren't skills.
    assert "base" not in skills
    assert "registry" not in skills
    assert "adapters" not in skills


# ---- sensitivity ceiling enforcement happens in tool_loop, not the skill ----


def test_skill_result_dataclass_defaults():
    r = SkillResult(ok=True, content="hello")
    assert r.error is None
    assert r.artifacts == {}
