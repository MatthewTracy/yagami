from __future__ import annotations

import json

from ..router.schema import Sensitivity
from ..storage.db import get_db
from .engine import PolicyEngine
from .models import PolicyContext


def _json_object(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def replay_decisions(
    *,
    engine: PolicyEngine,
    project_id: str,
    decision_ids: list[int],
) -> list[dict]:
    if not decision_ids:
        return []
    placeholders = ",".join("?" for _ in decision_ids)
    sql = (
        "SELECT id, backend, classification, policy_decision, request_context"
        f" FROM decisions WHERE channel='gateway' AND project_id=? AND id IN ({placeholders})"
        " ORDER BY id"
    )
    db = get_db()
    async with db.execute(sql, (project_id, *decision_ids)) as cursor:
        rows = await cursor.fetchall()

    results: list[dict] = []
    for row in rows:
        classification = _json_object(row["classification"])
        previous = _json_object(row["policy_decision"])
        audit_context = _json_object(row["request_context"])
        sensitivity_value = (
            previous.get("lineage", {}).get("effective_sensitivity")
            if isinstance(previous.get("lineage"), dict)
            else None
        ) or classification.get("sensitivity", "none")
        try:
            sensitivity = Sensitivity(sensitivity_value)
        except (TypeError, ValueError):
            sensitivity = Sensitivity.NONE
        try:
            sensitivity_hint = (
                Sensitivity(audit_context["sensitivity_hint"])
                if audit_context.get("sensitivity_hint")
                else None
            )
        except (TypeError, ValueError):
            sensitivity_hint = None
        context = PolicyContext(
            project_id=project_id,
            purpose=str(audit_context.get("purpose") or "general"),
            jurisdiction=(
                str(audit_context["jurisdiction"])
                if audit_context.get("jurisdiction") is not None
                else None
            ),
            session_id=(
                str(audit_context["client_session_id"])
                if audit_context.get("client_session_id") is not None
                else None
            ),
            sensitivity_hint=sensitivity_hint,
            requested_tools=[str(tool) for tool in audit_context.get("requested_tools", [])],
        )
        current = engine.evaluate(
            context=context,
            detected_sensitivity=sensitivity,
            candidate_backend=str(row["backend"]),
        )
        comparison = {
            "policy_hash": [previous.get("policy_hash"), current.policy_hash],
            "route": [previous.get("route"), current.route.value],
            "allowed_backends": [previous.get("allowed_backends"), current.allowed_backends],
            "denied": [previous.get("denied"), current.denied],
            "transform": [previous.get("transform"), current.transform.value],
            "retention_days": [previous.get("retention_days"), current.retention_days],
        }
        changed_fields = [name for name, values in comparison.items() if values[0] != values[1]]
        results.append(
            {
                "decision_id": int(row["id"]),
                "candidate_backend": row["backend"],
                "previous_policy_hash": previous.get("policy_hash"),
                "current_policy": current.passport(),
                "changed": bool(changed_fields),
                "changed_fields": changed_fields,
            }
        )
    return results
