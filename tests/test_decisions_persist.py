from __future__ import annotations

import pytest

from yagami.chat.session import SessionStore
from yagami.telemetry.decisions import list_decisions, persist_decision, scrub


@pytest.mark.asyncio
async def test_persist_and_list(fresh_db):
    store = SessionStore()
    sid = await store.new_session()
    decision = {
        "backend": "ollama",
        "is_local": True,
        "reason": "default (ollama)",
        "classification": {
            "intent": "simple_qa",
            "sensitivity": "none",
            "complexity": "low",
            "source": "classifier",
        },
    }
    await persist_decision(session_id=sid, user_text="what is 2+2?", decision=decision)
    rows = await list_decisions(session_id=sid)
    assert len(rows) == 1
    assert rows[0]["backend"] == "ollama"
    assert rows[0]["is_local"] is True
    assert rows[0]["source"] == "classifier"


@pytest.mark.asyncio
async def test_ledger_scrubs_phi(fresh_db):
    store = SessionStore()
    sid = await store.new_session()
    raw = "My SSN is 123-45-6789 and phone is 415-555-0199, please summarize"
    decision = {
        "backend": "ollama",
        "is_local": True,
        "reason": "sensitivity=phi_medical; forced local (ollama)",
        "classification": {
            "intent": "complex_reasoning",
            "sensitivity": "phi_medical",
            "complexity": "high",
            "source": "classifier",
        },
    }
    await persist_decision(session_id=sid, user_text=raw, decision=decision)
    rows = await list_decisions(session_id=sid)
    preview = rows[0]["scrubbed_preview"]
    assert "123-45-6789" not in preview
    assert "415-555-0199" not in preview
    assert "[REDACTED]" in preview


@pytest.mark.asyncio
async def test_phi_ledger_routes_local_only(fresh_db):
    store = SessionStore()
    sid = await store.new_session()
    decision = {
        "backend": "ollama",
        "is_local": True,
        "reason": "sensitivity=phi; forced local (ollama)",
        "classification": {
            "intent": "complex_reasoning",
            "sensitivity": "phi",
            "complexity": "high",
            "source": "classifier",
        },
    }
    await persist_decision(session_id=sid, user_text="patient note", decision=decision)
    rows = await list_decisions(session_id=sid)
    for r in rows:
        if "phi" in r["classification"].get("sensitivity", ""):
            assert r["is_local"] is True, "PHI decision must show local backend in ledger"


def test_scrub_patterns():
    assert "[REDACTED]" in scrub("contact me at alice@example.com")
    assert "[REDACTED]" in scrub("call 415-555-0199")
    assert "[REDACTED]" in scrub("SSN 123-45-6789")
    assert "[REDACTED]" in scrub("card 4111111111111111")
    assert scrub("nothing sensitive here") == "nothing sensitive here"
