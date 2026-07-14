from __future__ import annotations

import json

from yagami.policy import PolicyContext, PolicyEngine
from yagami.router.schema import Sensitivity


def test_policy_engine_merges_restrictive_effects(tmp_path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(
            {
                "id": "enterprise",
                "version": "2",
                "defaults": {
                    "route": "auto",
                    "allowed_backends": ["local-a", "cloud-a"],
                    "retention_days": 90,
                },
                "rules": [
                    {
                        "id": "project-limit",
                        "priority": 100,
                        "match": {"projects": ["health"]},
                        "effect": {
                            "allowed_backends": ["local-a"],
                            "output_action": "redact",
                            "retention_days": 30,
                        },
                    },
                    {
                        "id": "phi-local",
                        "priority": 200,
                        "match": {"sensitivities": ["phi"]},
                        "effect": {
                            "route": "local",
                            "output_action": "block",
                            "retention_days": 7,
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    engine = PolicyEngine(path)
    result = engine.evaluate(
        context=PolicyContext(project_id="health"),
        detected_sensitivity=Sensitivity.PHI,
        candidate_backend="cloud-a",
    )

    assert result.matched_rules == ["phi-local", "project-limit"]
    assert result.route.value == "local"
    assert result.allowed_backends == ["local-a"]
    assert result.retention_days == 7
    assert result.output_action.value == "block"
    assert result.policy_hash.startswith("sha256:")


def test_caller_hint_can_only_raise_effective_sensitivity(tmp_path) -> None:
    engine = PolicyEngine(tmp_path / "missing.yaml")
    result = engine.evaluate(
        context=PolicyContext(project_id="p", sensitivity_hint=Sensitivity.SECRET),
        detected_sensitivity=Sensitivity.NONE,
        candidate_backend="cloud",
    )
    assert result.effective_sensitivity == Sensitivity.SECRET
    assert result.route.value == "local"


def test_policy_hot_reloads_when_file_changes(tmp_path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text("id: p\nversion: '1'\ndefaults: {route: auto}\n", encoding="utf-8")
    engine = PolicyEngine(path)
    first = engine.policy_hash
    path.write_text("id: p\nversion: '2'\ndefaults: {route: local}\n", encoding="utf-8")
    path.touch()
    assert engine.document.version == "2"
    assert engine.policy_hash != first
