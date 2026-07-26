from __future__ import annotations

import hashlib
import json
from pathlib import Path
from collections.abc import Iterable

import yaml

from ..router.policy import stickier
from ..backends.base import Capability, TrustZone
from ..router.schema import DataLabel, Sensitivity
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


def _matches(
    match: PolicyMatch,
    context: PolicyContext,
    sensitivity: Sensitivity,
    *,
    data_labels: set[DataLabel],
    destination_zone: TrustZone,
) -> bool:
    if not _matches_values(match.projects, context.project_id):
        return False
    if not _matches_values(match.purposes, context.purpose):
        return False
    if match.sensitivities and sensitivity not in match.sensitivities:
        return False
    if match.data_labels and not set(match.data_labels).intersection(data_labels):
        return False
    if not _matches_values(match.jurisdictions, context.jurisdiction):
        return False
    if match.tools and not set(match.tools).intersection(context.requested_tools):
        return False
    if match.destination_zones and destination_zone not in match.destination_zones:
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

    def __init__(
        self,
        path: Path,
        *,
        public_key_path: Path | None = None,
        require_signature: bool = False,
    ) -> None:
        if require_signature and public_key_path is None:
            raise ValueError("signature-required policy loading needs a public verification key")
        self.path = path
        self.public_key_path = public_key_path
        self.require_signature = require_signature
        self._source_digest: str | None = None
        self._document = default_policy()
        self._hash = _canonical_hash(self._document)
        self._bundle_hash: str | None = None
        self._signing_key_sha256: str | None = None
        self._signature_verified = False
        self.reload(force=True)

    @property
    def document(self) -> PolicyDocument:
        self.reload()
        return self._document

    @property
    def policy_hash(self) -> str:
        self.reload()
        return self._hash

    @property
    def signature_verified(self) -> bool:
        self.reload()
        return self._signature_verified

    @property
    def policy_bundle_hash(self) -> str | None:
        self.reload()
        return self._bundle_hash

    def reload(self, *, force: bool = False) -> bool:
        if not self.path.exists():
            if self.require_signature:
                raise FileNotFoundError(f"required signed policy bundle not found: {self.path}")
            return False
        source = self.path.read_bytes()
        source_digest = hashlib.sha256(source).hexdigest()
        if not force and source_digest == self._source_digest:
            return False
        policy_source = source
        manifest: dict | None = None
        if self.public_key_path is not None:
            if not self.public_key_path.exists():
                raise FileNotFoundError(
                    f"policy verification key not found: {self.public_key_path}"
                )
            from .bundle import read_verified_bundle

            manifest, policy_source = read_verified_bundle(self.path, self.public_key_path)
        elif self.require_signature:
            raise ValueError("unsigned policy source refused by signature-required mode")

        raw = yaml.safe_load(policy_source.decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"policy file {self.path} must contain a YAML/JSON object")
        document = PolicyDocument.model_validate(raw)
        canonical_hash = _canonical_hash(document)
        if manifest is not None:
            policy_manifest = manifest.get("policy")
            if not isinstance(policy_manifest, dict):
                raise ValueError("signed policy bundle is missing policy metadata")
            if policy_manifest.get("canonical_hash") != canonical_hash:
                raise ValueError("signed policy canonical hash does not match the manifest")
            if policy_manifest.get("id") != document.id:
                raise ValueError("signed policy ID does not match the manifest")
            if policy_manifest.get("version") != document.version:
                raise ValueError("signed policy version does not match the manifest")
        self._document = document
        self._hash = canonical_hash
        self._source_digest = source_digest
        self._bundle_hash = (
            "sha256:" + hashlib.sha256(source).hexdigest() if manifest is not None else None
        )
        self._signing_key_sha256 = (
            str(manifest["signing_key_sha256"]) if manifest is not None else None
        )
        self._signature_verified = manifest is not None
        return True

    def evaluate(
        self,
        *,
        context: PolicyContext,
        detected_sensitivity: Sensitivity,
        candidate_backend: str,
        data_labels: Iterable[DataLabel] = (),
        candidate_trust_zone: TrustZone = TrustZone.EXTERNAL,
        required_capabilities: Iterable[Capability] = (),
    ) -> PolicyEvaluation:
        document = self.document
        effective_sensitivity = stickier(detected_sensitivity, context.sensitivity_hint)
        effective_labels = set(data_labels) | context.data_labels
        if effective_sensitivity in {Sensitivity.PHI, Sensitivity.PHI_MEDICAL}:
            effective_labels.add(DataLabel.PHI)
        elif effective_sensitivity == Sensitivity.SECRET:
            effective_labels.add(DataLabel.SECRET)
        matched = [
            rule
            for rule in sorted(document.rules, key=lambda item: (-item.priority, item.id))
            if rule.enabled
            and _matches(
                rule.match,
                context,
                effective_sensitivity,
                data_labels=effective_labels,
                destination_zone=candidate_trust_zone,
            )
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

        zone_sets = [
            set(value)
            for value in [
                defaults.allowed_trust_zones,
                *(rule.effect.allowed_trust_zones for rule in matched),
            ]
            if value is not None
        ]
        allowed_trust_zones: list[TrustZone] | None = None
        if zone_sets:
            allowed_trust_zones = sorted(set.intersection(*zone_sets), key=lambda item: item.value)

        capabilities = set(required_capabilities) | set(defaults.required_capabilities)
        capabilities.update(
            capability for rule in matched for capability in rule.effect.required_capabilities
        )

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
        denied = route == RoutePolicy.DENY or allowed_backends == [] or allowed_trust_zones == []

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
            policy_bundle_hash=self._bundle_hash,
            signing_key_sha256=self._signing_key_sha256,
            signature_verified=self._signature_verified,
            mode=document.mode,
            matched_rules=[rule.id for rule in matched],
            detected_sensitivity=detected_sensitivity,
            effective_sensitivity=effective_sensitivity,
            data_labels=sorted(effective_labels, key=lambda item: item.value),
            route=route,
            allowed_backends=allowed_backends,
            allowed_trust_zones=allowed_trust_zones,
            required_capabilities=sorted(capabilities, key=lambda item: item.value),
            candidate_trust_zone=candidate_trust_zone,
            denied_tools=denied_tools,
            require_approval_for_tools=approval_tools,
            transform=transform,
            output_action=output_action,
            retention_days=retention_days,
            candidate_backend=candidate_backend,
            denied=denied,
            reasons=reasons,
        )
