from __future__ import annotations

import csv
import io
import json
import logging

import pytest

from yagami.chat.session import SessionStore
from yagami.storage.db import close_db, open_db
from yagami.telemetry.decisions import (
    export_decisions_csv,
    list_decisions,
    log_decision,
    persist_decision,
    scrub,
    update_decision_timings,
)


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
async def test_ledger_is_content_free(fresh_db):
    store = SessionStore()
    sid = await store.new_session()
    raw = (
        "Jane Doe at 100 Main Street has CHF. SSN 123-45-6789. "
        "Phone 415-555-0199. SECRET_TOKEN=abc123. "
        "Retrieved: internal acquisition plan. Tool args: transfer(account=42)."
    )
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
    assert "scrubbed_preview" not in rows[0]
    serialized = json.dumps(rows[0], sort_keys=True)
    for secret in (
        raw,
        "Jane Doe",
        "100 Main Street",
        "CHF",
        "123-45-6789",
        "415-555-0199",
        "abc123",
        "internal acquisition plan",
        "transfer(account=42)",
    ):
        assert secret not in serialized


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


def test_decision_log_is_content_free(tmp_path, caplog):
    raw = "Patient Jane Doe lives at 100 Main Street and has CHF"
    path = tmp_path / "decisions.jsonl"
    with caplog.at_level(logging.INFO, logger="yagami.decisions"):
        log_decision(
            session_id="session-1",
            user_text=raw,
            decision={"backend": "ollama", "classification": {"sensitivity": "phi"}},
            log_path=path,
        )
    assert raw not in path.read_text(encoding="utf-8")
    assert raw not in caplog.text
    record = json.loads(path.read_text(encoding="utf-8"))
    assert "user_text_preview" not in record


@pytest.mark.asyncio
async def test_content_free_migration_purges_existing_previews(fresh_db, tmp_path):
    await fresh_db.execute(
        "INSERT INTO sessions(id, created_at, updated_at) VALUES('legacy', 1, 1)"
    )
    await fresh_db.execute(
        "INSERT INTO decisions("
        "session_id, created_at, backend, is_local, reason, classification,"
        "scrubbed_preview, source"
        ") VALUES('legacy', 1, 'ollama', 1, 'legacy', '{}',"
        "'Patient Jane Doe lives at 100 Main Street', 'legacy')"
    )
    await fresh_db.execute(
        "DELETE FROM schema_migrations WHERE version='014_content_free_decisions'"
    )
    await fresh_db.commit()
    db_path = tmp_path / "yagami_test.db"
    await close_db()

    reopened = await open_db(db_path)
    async with reopened.execute(
        "SELECT scrubbed_preview FROM decisions WHERE session_id='legacy'"
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None
    assert row["scrubbed_preview"] == ""


# ---- Audit export (CSV) ----


@pytest.mark.asyncio
async def test_export_csv_includes_cost_and_token_columns(fresh_db):
    """list_decisions() intentionally omits tokens_in/out and cost_usd (not
    shown in the ledger table) - the audit export is the full record and
    must include them."""
    store = SessionStore()
    sid = await store.new_session()
    decision = {
        "backend": "anthropic",
        "is_local": False,
        "reason": "complexity=high",
        "classification": {
            "intent": "complex_reasoning",
            "sensitivity": "none",
            "complexity": "high",
            "source": "classifier",
        },
    }
    decision_id = await persist_decision(session_id=sid, user_text="explain X", decision=decision)
    await update_decision_timings(
        decision_id, tokens_in=120, tokens_out=340, cost_usd=0.0123, total_ms=850
    )

    csv_text = await export_decisions_csv(session_id=sid)
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert len(rows) == 1
    row = rows[0]
    assert row["tokens_in"] == "120"
    assert row["tokens_out"] == "340"
    assert row["cost_usd"] == "0.0123"
    assert row["backend"] == "anthropic"
    assert row["intent"] == "complex_reasoning"
    assert row["feedback_rating"] == ""  # no feedback given


@pytest.mark.asyncio
async def test_export_csv_is_content_free(fresh_db):
    store = SessionStore()
    sid = await store.new_session()
    decision = {
        "backend": "ollama",
        "is_local": True,
        "reason": "sensitivity=phi; forced local",
        "classification": {
            "intent": "simple_qa",
            "sensitivity": "phi",
            "complexity": "low",
            "source": "classifier",
        },
    }
    await persist_decision(session_id=sid, user_text="my SSN is 123-45-6789", decision=decision)
    csv_text = await export_decisions_csv(session_id=sid)
    assert "123-45-6789" not in csv_text
    assert "scrubbed_preview" not in csv_text
    assert "[REDACTED]" not in csv_text


@pytest.mark.asyncio
async def test_export_csv_scoped_to_session(fresh_db):
    store = SessionStore()
    sid_a = await store.new_session()
    sid_b = await store.new_session()
    decision = {
        "backend": "ollama",
        "is_local": True,
        "reason": "default",
        "classification": {"intent": "simple_qa", "sensitivity": "none", "complexity": "low"},
    }
    await persist_decision(session_id=sid_a, user_text="a", decision=decision)
    await persist_decision(session_id=sid_b, user_text="b", decision=decision)

    csv_text = await export_decisions_csv(session_id=sid_a)
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert len(rows) == 1
    assert rows[0]["session_id"] == sid_a
