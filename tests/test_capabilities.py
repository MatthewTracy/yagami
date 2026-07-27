from __future__ import annotations

import json

import pytest

from yagami.backends.base import Capability, Pricing, TrustZone
from yagami.capabilities import runtime_capabilities
from yagami.config import get_settings
from yagami.storage.db import close_db, open_db


class _Backend:
    name = "local-test"
    capabilities = {Capability.TEXT, Capability.TOOLS}
    trust_zone = TrustZone.DEVICE
    is_local = True
    pricing = Pricing()
    api_key = "do-not-include"
    url = "https://private.example.invalid/v1"


@pytest.mark.asyncio
async def test_capability_document_is_stable_and_content_free(tmp_path, monkeypatch):
    monkeypatch.setenv("YAGAMI_COORDINATION_URL", "")
    get_settings.cache_clear()
    await open_db(tmp_path / "capabilities.db")
    try:
        document = runtime_capabilities(
            backends={"local-test": _Backend()},
            embedder_available=False,
        )
    finally:
        await close_db()
        get_settings.cache_clear()

    assert document["object"] == "yagami.capabilities"
    assert document["schema_version"] == "1.0"
    assert document["storage"]["dialect"] == "sqlite"
    assert document["storage"]["multi_replica_ready"] is False
    assert document["providers"] == [
        {
            "id": "local-test",
            "trust_zone": "device",
            "capabilities": ["text", "tools"],
        }
    ]
    assert document["governance"]["content_free_evidence"] is True
    assert document["governance"]["telemetry_default"] == "disabled"
    assert document["mcp"]["experimental"] == {"tasks": False, "elicitation": False}
    serialized = json.dumps(document)
    assert "do-not-include" not in serialized
    assert "private.example.invalid" not in serialized
