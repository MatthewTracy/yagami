from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
import pytest_asyncio

from yagami.backends.base import Message
from yagami.chat.session import SessionStore
from yagami.governance import PrivacyTransformer, TransformationSession, generate_transform_key
from yagami.memory import documents, store
from yagami.memory.retriever import Retriever
from yagami.responses import (
    append_response_event,
    create_response_job,
    get_response_job,
    list_response_events,
)
from yagami.router.schema import Sensitivity
from yagami.storage.db import close_db, get_db, open_db
from yagami.telemetry.audit import AuditLedger
from yagami.telemetry.decisions import persist_decision

_POSTGRES_URL = os.getenv("YAGAMI_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="set YAGAMI_TEST_POSTGRES_URL to run PostgreSQL integration tests",
)


class FixedEmbedder:
    async def embed(self, text: str) -> list[float] | None:
        _ = text
        return [1.0] + [0.0] * 383


@pytest_asyncio.fixture
async def postgres_db(tmp_path: Path):
    await open_db(tmp_path / "unused.db", database_url=_POSTGRES_URL)
    db = get_db()
    await db.execute(
        """
        TRUNCATE TABLE response_events, response_jobs, audit_outbox,
          audit_key_epochs, audit_events, privacy_tokens, tool_schema_pins,
          tool_approvals, kb_documents_vec, kb_documents, observations_vec,
          observations, feedback, decisions, message_attachments, messages,
          sessions RESTART IDENTITY CASCADE
        """
    )
    await db.commit()
    yield db
    await close_db()


@pytest.mark.asyncio
async def test_postgres_core_evidence_memory_and_response_lifecycle(postgres_db) -> None:
    sessions = SessionStore()
    session_id = await sessions.new_session(project_id="integration")
    message_id = await sessions.append(
        session_id,
        Message(role="user", content="Remember the incident response checklist."),
    )
    assert message_id > 0

    decision_id = await persist_decision(
        session_id=session_id,
        user_text="private prompt is deliberately absent",
        decision={
            "backend": "ollama",
            "is_local": True,
            "reason": "private route",
            "classification": {"source": "test", "sensitivity": "none"},
        },
        project_id="integration",
    )
    assert decision_id > 0

    observation_ids = await store.queue_observation(
        session_id=session_id,
        role="assistant",
        text="The incident response checklist starts with containment and evidence preservation.",
        sensitivity=Sensitivity.NONE,
        project_id="integration",
    )
    assert observation_ids
    await store.write_embeddings(
        [(observation_id, [1.0] + [0.0] * 383) for observation_id in observation_ids]
    )
    hits = await Retriever(FixedEmbedder()).fetch(
        "incident response",
        project_id="integration",
    )
    assert hits and hits[0].project_id == "integration"

    transformer = PrivacyTransformer(key=generate_transform_key())
    transform_session = TransformationSession(
        request_id="tok_" + "d" * 32,
        project_id="integration",
        mode="tokenize",
    )
    transformed = await transformer.transform_text(
        "Contact postgres@example.com",
        session=transform_session,
    )
    assert "postgres@example.com" not in transformed
    assert (
        await transformer.rehydrate_from_vault(
            transformed,
            request_id=transform_session.request_id,
            project_id="integration",
        )
        == "Contact postgres@example.com"
    )

    audit = AuditLedger(key="postgres-audit-key-0123456789", required=True)
    event = await audit.append(
        project_id="integration",
        event_type="integration.test",
        payload={"outcome": "allowed"},
    )
    assert event["id"] > 0
    assert (await audit.verify(project_id="integration"))["valid"]

    await create_response_job(
        response_id="resp_postgres",
        project_id="integration",
        request_id="req_postgres",
        decision_id=decision_id,
        model="yagami",
        status="completed",
        messages=[Message(role="user", content="hello")],
        metadata={},
        previous_response_id=None,
        conversation_id="conversation-postgres",
        retention_days=1,
    )
    await append_response_event("resp_postgres", 0, {"type": "response.created"})
    await append_response_event("resp_postgres", 0, {"type": "duplicate"})
    assert (await get_response_job("resp_postgres", "integration"))["status"] == "completed"
    assert len(await list_response_events("resp_postgres", "integration")) == 1


@pytest.mark.asyncio
async def test_postgres_document_vector_and_full_text_search(postgres_db) -> None:
    source = "postgres-integration.md"
    await documents._replace_document(
        source,
        ["Yagami governs retrieval and agent tool execution."],
        embedder=FixedEmbedder(),
    )
    hits = await documents.search("agent tool execution", embedder=FixedEmbedder())
    assert hits
    assert hits[0]["source_path"] == source


@pytest.mark.asyncio
async def test_postgres_concurrent_audit_writers_keep_one_valid_chain(postgres_db) -> None:
    first = AuditLedger(key="postgres-audit-key-0123456789", required=True)
    second = AuditLedger(key="postgres-audit-key-0123456789", required=True)

    events = await asyncio.gather(
        *(
            (first if index % 2 == 0 else second).append(
                project_id="concurrent-audit",
                event_type="integration.concurrent",
                payload={"sequence": index},
            )
            for index in range(20)
        )
    )

    assert len({event["id"] for event in events}) == 20
    result = await first.verify(project_id="concurrent-audit")
    assert result["valid"] is True
    assert result["events"] == 20
