"""Stable, content-free runtime capability discovery."""

from __future__ import annotations

from typing import Any

from . import __version__
from .backends.base import TrustZone
from .config import get_settings
from .governance.context_firewall import DEFAULT_DETECTORS
from .router.schema import DataLabel
from .skills.mcp_manager import get_manager
from .storage.db import get_db

CAPABILITY_SCHEMA_VERSION = "1.0"


def runtime_capabilities(
    *,
    backends: dict[str, Any],
    embedder_available: bool | None = None,
) -> dict[str, Any]:
    """Describe runtime support without leaking endpoints or credentials."""

    settings = get_settings()
    manager = get_manager()
    catalog = (
        manager.catalog()
        if manager is not None
        else {"tools": [], "resources": [], "prompts": [], "quarantined": []}
    )
    if embedder_available is None:
        embedder_available = any(
            "embeddings" in {capability.value for capability in backend.capabilities}
            for backend in backends.values()
        )

    providers = [
        {
            "id": name,
            "trust_zone": backend.trust_zone.value,
            "capabilities": sorted(capability.value for capability in backend.capabilities),
        }
        for name, backend in sorted(backends.items())
    ]
    coordination = "redis" if settings.coordination_url else "local"

    return {
        "object": "yagami.capabilities",
        "schema_version": CAPABILITY_SCHEMA_VERSION,
        "server_version": __version__,
        "apis": {
            "openai_compatible": [
                "models",
                "embeddings",
                "chat.completions",
                "responses",
            ],
            "governance": [
                "policy.discovery",
                "policy.preview",
                "policy.replay",
                "privacy.transform",
                "privacy.rehydrate",
                "audit.verify",
                "tool_approvals",
            ],
            "response_lifecycle": [
                "background",
                "cancel",
                "event_replay",
                "resumable_streaming",
            ],
        },
        "storage": {
            "dialect": get_db().dialect,
            "supported": ["sqlite", "postgresql"],
            "coordination": coordination,
            "multi_replica_ready": get_db().dialect == "postgresql" and coordination == "redis",
            "migrations": "alembic",
            "backup_verification": True,
        },
        "governance": {
            "trust_zones": [zone.value for zone in TrustZone],
            "data_labels": [label.value for label in DataLabel],
            "stable_reason_codes": True,
            "signed_policy_bundles": True,
            "content_free_evidence": True,
            "telemetry_default": "disabled",
        },
        "detectors": [
            {"id": detector.name, "version": detector.version} for detector in DEFAULT_DETECTORS
        ],
        "providers": providers,
        "retrieval_and_memory": {
            "retrieval": True,
            "embeddings": bool(embedder_available),
            "namespaces": True,
            "provenance": True,
            "labels": True,
            "ttl": True,
            "quarantine": True,
            "policy_enforcement": True,
        },
        "tools": {
            "provider_neutral_execution": True,
            "schema_pinning": True,
            "drift_detection": True,
            "identity_bound_approvals": True,
            "configured_mcp_tools": len(catalog["tools"]),
            "quarantined_mcp_capabilities": len(catalog["quarantined"]),
        },
        "mcp": {
            "server": settings.mcp_server_enabled,
            "aggregation": True,
            "stable_namespaced_identities": True,
            "tools": True,
            "resources": True,
            "prompts": True,
            "schema_pinning": True,
            "ssrf_protection": True,
            "oauth": ["client_credentials"],
            "experimental": {
                "tasks": False,
                "elicitation": False,
            },
        },
    }
