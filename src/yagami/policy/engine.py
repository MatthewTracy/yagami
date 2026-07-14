from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from ..router.policy import stickier
from ..router.schema import Sensitivity
from .models import (
    PolicyContext,
    PolicyDefaults,
    PolicyDocument,
    PolicyEvaluation,
    PolicyMatch,
    OutputPolicy,
    RoutePolicy,
)


def _canonical_hash(document: PolicyDocument) -> str:
    encoded = json.dumps(
        document.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _matches_values(patterns: list[str], value: str | None) -> bool:
    if not patterns:
        return True
    if value is None:
        return False
    return "*" in patterns or value in patterns


def _matches(match: PolicyMatch, context: PolicyContext, sensitivity: Sensitivity) -> bool:
    if not _matches_values(match.projects, context.project_id):
        return False
    if not _matches_values(match.purposes, context.purpose):
        return False
    if match.sensitivities and sensitivity not in match.sensitivities:
        return False
    if not _matches_values(match.jurisdictions, context.jurisdiction):
        return False
    if match.tools and not set(match.tools).intersection(context.requested_tools):
        return False
    return True


def default_policy() -> PolicyDocument:
    return PolicyDocument.model_validate(
        {
            "id": "yagami-default",
            "version": "1.0.0",
            "mode": "enforce",
            "defaults": {
                "route": "auto",
                "transform": "none",
                "retention_days": 30,
                "require_approval_for_tools": [
                    "file.write",
                    "file.delete",
                    "email.send",
                    "payment.create",
                    "sql.execute",
                ],
            },
            "rules": [
                {
                    "id": "sensitive-data-local",
                    "description": "PHI and secrets remain on a local backend.",
                    "priority": 1000,
                    "match": {"sensitivities": ["phi", "phi_medical", "secret"]},
                    "effect": {"route": "local", "retention_days": 7},
                }
            ],
        }
    )


class PolicyEngine:
    """Hot-reloaded, deterministic policy evaluation with restrictive merging."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._source_digest: str | None = None
        self._document = default_policy()
        self._hash = _canonical_hash(self._document)
        self.reload(force=True)

    @property
    def document(self) -> PolicyDocument:
        self.reload()
        return self._document

    @property
    def policy_hash(self) -> str:
        self.reload()
        return self._hash

    def reload(self, *, force: bool = False) -> bool:
        if not self.path.exists():
            return False
        source = self.path.read_bytes()
        source_digest = hashlib.sha256(source).hexdigest()
        if not force and source_digest == self._source_digest:
            return False
        raw = yaml.safe_load(source.decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"policy file {self.path} must contain a YAML/JSON object")
        document = PolicyDocument.model_validate(raw)
        self._document = document
        self._hash = _canonical_hash(document)
        self._source_digest = source_digest
        return True

    def evaluate(
        self,
        *,
        context: PolicyContext,
        detected_sensitivity: Sensitivity,
        candidate_backend: str,
    ) -> PolicyEvaluation:
        document = self.document
        effective_sensitivity = stickier(detected_sensitivity, context.sensitivity_hint)
        matched = [
            rule
            for rule in sorted(document.rules, key=lambda item: (-item.priority, item.id))
            if rule.enabled and _matches(rule.match, context, effective_sensitivity)
        ]
        defaults: PolicyDefaults = document.defaults

        route = next(
            (rule.effect.route for rule in matched if rule.effect.route is not None),
            defaults.route,
        )
        transform = next(
            (rule.effect.transform for rule in matched if rule.effect.transform is not None),
            defaults.transform,
        )
        output_rank = {
            OutputPolicy.ALLOW: 0,
            OutputPolicy.REDACT: 1,
            OutputPolicy.BLOCK: 2,
        }
        output_candidates = [
            defaults.output_action,
            *(
                rule.effect.output_action
                for rule in matched
                if rule.effect.output_action is not None
            ),
        ]
        output_action = max(output_candidates, key=output_rank.__getitem__)

        allowed_sets = [
            set(value)
            for value in [
                defaults.allowed_backends,
                *(rule.effect.allowed_backends for rule in matched),
            ]
            if value is not None
        ]
        allowed_backends: list[str] | None = None
        if allowed_sets:
            allowed_backends = sorted(set.intersection(*allowed_sets))

        denied_tools = sorted(
            set(defaults.denied_tools).union(*(set(rule.effect.denied_tools) for rule in matched))
        )
        approval_tools = sorted(
            set(defaults.require_approval_for_tools).union(
                *(set(rule.effect.require_approval_for_tools) for rule in matched)
            )
        )
        retention_candidates = [
            defaults.retention_days,
            *(
                rule.effect.retention_days
                for rule in matched
                if rule.effect.retention_days is not None
            ),
        ]
        retention_days = min(retention_candidates)
        denied = route == RoutePolicy.DENY or allowed_backends == []

        reasons = [f"matched policy rule {rule.id}" for rule in matched]
        if not matched:
            reasons.append("used policy defaults")
        if effective_sensitivity != detected_sensitivity:
            reasons.append(
                f"caller sensitivity hint raised {detected_sensitivity.value} to "
                f"{effective_sensitivity.value}"
            )

        return PolicyEvaluation(
            policy_id=document.id,
            policy_version=document.version,
            policy_hash=self._hash,
            mode=document.mode,
            matched_rules=[rule.id for rule in matched],
            detected_sensitivity=detected_sensitivity,
            effective_sensitivity=effective_sensitivity,
            route=route,
            allowed_backends=allowed_backends,
            denied_tools=denied_tools,
            require_approval_for_tools=approval_tools,
            transform=transform,
            output_action=output_action,
            retention_days=retention_days,
            candidate_backend=candidate_backend,
            denied=denied,
            reasons=reasons,
        )
