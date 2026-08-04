from __future__ import annotations

import hashlib

import pytest

from yagami.governance import (
    LineageGraph,
    PrivacyTransformer,
    TransformationError,
    TransformationSession,
    generate_transform_key,
)
from yagami.router.schema import Sensitivity
from yagami.backends.base import Message


@pytest.mark.asyncio
async def test_tokenization_encrypts_values_and_rehydrates_once(fresh_db) -> None:
    transformer = PrivacyTransformer(key=generate_transform_key(), ttl_seconds=3600)
    session = TransformationSession(
        request_id="tok_" + "a" * 32,
        project_id="health",
        mode="tokenize",
    )
    original = "Email jane.patient@example.com or call 415-555-0199."
    transformed = await transformer.transform_text(original, session=session)

    assert "jane.patient@example.com" not in transformed
    assert "415-555-0199" not in transformed
    assert "[YGM_EMAIL_1]" in transformed
    assert "[YGM_PHONE_1]" in transformed

    async with fresh_db.execute(
        "SELECT nonce, ciphertext, value_hash, wrapped_key, wrapping_key_id, key_epoch"
        " FROM privacy_tokens ORDER BY id"
    ) as cursor:
        rows = await cursor.fetchall()
    assert len(rows) == 2
    assert all(original.encode() not in row["ciphertext"] for row in rows)
    assert all(row["wrapped_key"] and len(row["wrapped_key"]) > 32 for row in rows)
    assert {row["wrapping_key_id"] for row in rows} == {"local-1"}
    assert {row["key_epoch"] for row in rows} == {1}
    assert rows[0]["value_hash"] != hashlib.sha256("jane.patient@example.com".encode()).hexdigest()

    restored = await transformer.rehydrate_from_vault(
        "Contact [YGM_EMAIL_1] or [YGM_PHONE_1].",
        request_id=session.request_id,
        project_id="health",
    )
    assert restored == "Contact jane.patient@example.com or 415-555-0199."
    async with fresh_db.execute("SELECT COUNT(*) FROM privacy_tokens") as cursor:
        assert (await cursor.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_envelope_key_rotation_reads_previous_epoch(fresh_db) -> None:
    old_key = generate_transform_key()
    new_key = generate_transform_key()
    original_transformer = PrivacyTransformer(
        key=old_key,
        key_id="vault-key-2026-q2",
        key_epoch=2,
    )
    session = TransformationSession(
        request_id="tok_" + "c" * 32,
        project_id="rotating",
        mode="tokenize",
    )
    transformed = await original_transformer.transform_text(
        "Email rotate@example.com",
        session=session,
    )

    rotated_transformer = PrivacyTransformer(
        key=new_key,
        key_id="vault-key-2026-q3",
        key_epoch=3,
        previous_keys={"vault-key-2026-q2": old_key},
    )
    restored = await rotated_transformer.rehydrate_from_vault(
        transformed,
        request_id=session.request_id,
        project_id=session.project_id,
    )
    assert restored == "Email rotate@example.com"


@pytest.mark.asyncio
async def test_vault_is_project_scoped(fresh_db) -> None:
    transformer = PrivacyTransformer(key=generate_transform_key())
    session = TransformationSession(
        request_id="tok_" + "b" * 32,
        project_id="project-a",
        mode="tokenize",
    )
    transformed = await transformer.transform_text("SSN 123-45-6789", session=session)
    with pytest.raises(TransformationError, match="not found"):
        await transformer.rehydrate_from_vault(
            transformed,
            request_id=session.request_id,
            project_id="project-b",
        )


@pytest.mark.asyncio
async def test_redaction_does_not_create_reversible_tokens(fresh_db) -> None:
    transformer = PrivacyTransformer(key="")
    session = TransformationSession(request_id="r", project_id="p", mode="redact")
    transformed = await transformer.transform_text("Use card 4111 1111 1111 1111", session=session)
    assert transformed == "Use card [REDACTED_CREDIT_CARD]"
    assert session.mapping == {}
    async with fresh_db.execute("SELECT COUNT(*) FROM privacy_tokens") as cursor:
        assert (await cursor.fetchone())[0] == 0


def test_lineage_tracks_history_and_current_sensitivity_without_content() -> None:
    graph = LineageGraph.from_messages(
        request_id="ygm_test",
        messages=[
            Message(role="system", content="Be concise"),
            Message(role="user", content="Earlier MRN 00987654"),
            Message(role="assistant", content="Understood"),
            Message(role="user", content="Summarize it"),
        ],
        current_sensitivity=Sensitivity.NONE,
        caller_hint=Sensitivity.PHI,
    )
    summary = graph.summary()
    # Medical context in history remains medical even when the caller supplies
    # the broader PHI label and the current turn itself is nonsensitive.
    assert summary["effective_sensitivity"] == "phi_medical"
    assert len(summary["items"]) == 4
    encoded = str(summary)
    assert "00987654" not in encoded
    assert "Summarize it" not in encoded
    assert all(item["content_fingerprint"] for item in summary["items"])
