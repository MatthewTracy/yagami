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
    # OpenAI function names disallow dots - sanitized on the way out; the
    # tool loop maps them back (see test_tool_loop_openai.py).
    assert oai[0]["function"]["name"] == "calc__eval"
    assert "parameters" in oai[0]["function"]


# ---- registry ----


def test_registry_finds_first_party_skills():
    skills = discover_skills()
    assert "calc.eval" in skills
    assert "web.fetch" in skills
    assert "kb.recall" in skills
    assert "memory.remember" in skills
    assert "memory.recall" in skills
    # Helpers aren't skills.
    assert "base" not in skills
    assert "registry" not in skills
    assert "adapters" not in skills
    assert "mcp_manager" not in skills


# ---- kb.recall ----


@pytest.mark.asyncio
async def test_kb_recall_missing_query():
    from yagami.skills.kb_recall import KbRecall

    res = await KbRecall().run({}, _ctx())
    assert res.ok is False
    assert "query" in res.error.lower()


@pytest.mark.asyncio
async def test_kb_recall_no_matches(tmp_path, monkeypatch):
    from yagami.skills.kb_recall import KbRecall
    from yagami.storage.db import close_db, open_db

    await open_db(tmp_path / "kb.db")
    try:
        res = await KbRecall().run({"query": "anything"}, _ctx())
        assert res.ok is True
        assert "no matching" in res.content.lower()
    finally:
        await close_db()


def test_kb_recall_sensitivity_ceiling_matches_web_fetch():
    """kb.recall results flow to the cloud tool loop the same way web.fetch
    results do (see the module docstring) - it should be at least as
    conservative, not looser."""
    from yagami.skills.kb_recall import KbRecall

    assert KbRecall().sensitivity_ceiling == WebFetch().sensitivity_ceiling


# ---- memory.remember / memory.recall ----


@pytest.mark.asyncio
async def test_memory_remember_writes_observation(tmp_path):
    from yagami.skills.memory_remember import MemoryRemember
    from yagami.storage.db import close_db, get_db, open_db

    await open_db(tmp_path / "mem.db")
    try:
        res = await MemoryRemember().run(
            {"text": "The user's dog is named Mango and loves the beach."}, _ctx()
        )
        assert res.ok is True
        db = get_db()
        async with db.execute(
            "SELECT text, source_app FROM observations ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert "Mango" in row[0]
        assert row[1] == "skill"
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_memory_remember_secret_session_refused(tmp_path):
    """SECRET outranks the PHI_MEDICAL ceiling in the tool loop, but even a
    direct call must not write - the store's own gate rejects SECRET."""
    from yagami.skills.memory_remember import MemoryRemember
    from yagami.storage.db import close_db, open_db

    await open_db(tmp_path / "mem.db")
    try:
        res = await MemoryRemember().run(
            {"text": "api key is sk-super-secret-value-here"}, _ctx(Sensitivity.SECRET)
        )
        assert res.ok is False
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_memory_remember_missing_text():
    from yagami.skills.memory_remember import MemoryRemember

    res = await MemoryRemember().run({}, _ctx())
    assert res.ok is False
    assert "text" in res.error.lower()


@pytest.mark.asyncio
async def test_memory_recall_finds_other_sessions_via_fts(tmp_path):
    """No live Ollama here - the embedder fails, retrieval falls back to
    FTS5 (same path test_retriever.py exercises). The current session's own
    rows are excluded."""
    from yagami.skills.memory_recall import MemoryRecall
    from yagami.skills.memory_remember import MemoryRemember
    from yagami.storage.db import close_db, open_db

    await open_db(tmp_path / "mem.db")
    try:
        # Write from session "other" so recall from session "s1" can see it.
        other_ctx = SkillContext(session_id="other", session_sensitivity=Sensitivity.NONE)
        await MemoryRemember().run(
            {"text": "I love writing haiku about mango trees in spring."}, other_ctx
        )
        res = await MemoryRecall().run({"query": "haiku mango"}, _ctx())
        assert res.ok is True
        assert "mango" in res.content.lower()
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_memory_recall_no_matches(tmp_path):
    from yagami.skills.memory_recall import MemoryRecall
    from yagami.storage.db import close_db, open_db

    await open_db(tmp_path / "mem.db")
    try:
        res = await MemoryRecall().run({"query": "zzz nothing here"}, _ctx())
        assert res.ok is True
        assert "no matching" in res.content.lower()
    finally:
        await close_db()


def test_memory_recall_ceiling_matches_kb_recall():
    from yagami.skills.kb_recall import KbRecall
    from yagami.skills.memory_recall import MemoryRecall

    assert MemoryRecall().sensitivity_ceiling == KbRecall().sensitivity_ceiling


# ---- sensitivity ceiling enforcement happens in tool_loop, not the skill ----


def test_skill_result_dataclass_defaults():
    r = SkillResult(ok=True, content="hello")
    assert r.error is None
    assert r.artifacts == {}
