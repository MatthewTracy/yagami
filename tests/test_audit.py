from __future__ import annotations

import json

import pytest

from yagami.storage.db import get_db
from yagami.telemetry.audit import AuditLedger, HttpAuditSink


class RecordingSink:
    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[dict] = []
        self.fail = fail

    async def emit(self, event: dict) -> None:
        self.events.append(event)
        if self.fail:
            raise RuntimeError("sink unavailable")


@pytest.mark.asyncio
async def test_audit_chain_detects_payload_tampering(fresh_db) -> None:
    ledger = AuditLedger(key="audit-test-key-0123456789")
    first = await ledger.append(
        project_id="alpha",
        request_id="ygm_one",
        event_type="decision.created",
        payload={"backend": "local"},
    )
    second = await ledger.append(
        project_id="alpha",
        request_id="ygm_one",
        event_type="decision.completed",
        payload={"outcome": "ok"},
    )

    verified = await ledger.verify("alpha")
    assert verified["valid"] is True
    assert verified["events"] == 2
    assert verified["head"] == second["event_hash"]
    assert second["previous_hash"] == first["event_hash"]
    assert verified["key_id"].startswith("hmac-sha256:")

    await get_db().execute(
        "UPDATE audit_events SET payload=? WHERE id=?",
        (json.dumps({"outcome": "changed"}), second["id"]),
    )
    await get_db().commit()

    tampered = await ledger.verify("alpha")
    assert tampered["valid"] is False
    assert tampered["invalid_event_id"] == second["id"]
    assert "event hash mismatch" in tampered["reason"]


@pytest.mark.asyncio
async def test_audit_export_is_project_scoped(fresh_db) -> None:
    ledger = AuditLedger()
    await ledger.append(project_id="alpha", event_type="alpha.event", payload={"safe": True})
    await ledger.append(project_id="beta", event_type="beta.event", payload={"safe": True})

    exported = ledger.export_ndjson("alpha")
    records = [json.loads(line) for line in (await exported).splitlines()]

    assert len(records) == 1
    assert records[0]["project_id"] == "alpha"
    assert records[0]["event_type"] == "alpha.event"
    assert "beta.event" not in json.dumps(records)


def test_required_audit_requires_an_authentication_key() -> None:
    with pytest.raises(ValueError, match="YAGAMI_AUDIT_KEY"):
        AuditLedger(required=True)


@pytest.mark.asyncio
async def test_audit_sink_receives_tamper_evident_record(fresh_db) -> None:
    sink = RecordingSink()
    ledger = AuditLedger(key="audit-test-key-0123456789", sink=sink)

    result = await ledger.append(
        project_id="alpha", event_type="decision.created", payload={"backend": "local"}
    )

    assert sink.events == [result]
    assert result["project_id"] == "alpha"
    assert result["event_hash"]


@pytest.mark.asyncio
async def test_optional_audit_sink_failure_does_not_lose_local_event(fresh_db) -> None:
    ledger = AuditLedger(sink=RecordingSink(fail=True))

    result = await ledger.append(project_id="alpha", event_type="test", payload={})

    assert result["id"] > 0
    assert (await ledger.verify("alpha"))["valid"] is True


@pytest.mark.asyncio
async def test_required_audit_sink_failure_is_reported(fresh_db) -> None:
    ledger = AuditLedger(sink=RecordingSink(fail=True), sink_required=True)

    with pytest.raises(RuntimeError, match="sink unavailable"):
        await ledger.append(project_id="alpha", event_type="test", payload={})


def test_audit_sink_rejects_plaintext_remote_transport() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        HttpAuditSink("http://siem.example.test/events")
