"""Declarative policy regression tests for CI and local development."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..router.schema import Sensitivity
from .engine import PolicyEngine
from .models import (
    OutputPolicy,
    PolicyContext,
    RoutePolicy,
    TransformPolicy,
)


class PolicyTestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context: PolicyContext
    detected_sensitivity: Sensitivity = Sensitivity.NONE
    candidate_backend: str = Field(default="local", min_length=1, max_length=128)


class PolicyTestExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: RoutePolicy | None = None
    denied: bool | None = None
    transform: TransformPolicy | None = None
    output_action: OutputPolicy | None = None
    retention_days: int | None = Field(default=None, ge=0, le=3650)
    matched_rules: list[str] | None = None
    matched_rules_contain: list[str] = Field(default_factory=list)
    denied_tools_contain: list[str] = Field(default_factory=list)
    approval_tools_contain: list[str] = Field(default_factory=list)
    allowed_backends: list[str] | None = None


class PolicyTestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    input: PolicyTestInput
    expect: PolicyTestExpectation


class PolicyTestSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1, le=1)
    cases: list[PolicyTestCase] = Field(min_length=1, max_length=1000)


class PolicyTestResult(BaseModel):
    name: str
    passed: bool
    failures: list[str] = Field(default_factory=list)


def load_suite(path: Path) -> PolicyTestSuite:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"policy test file {path} must contain a YAML/JSON object")
    return PolicyTestSuite.model_validate(raw)


def _contains(label: str, actual: list[str], expected: list[str], failures: list[str]) -> None:
    missing = sorted(set(expected).difference(actual))
    if missing:
        failures.append(f"{label} missing {missing!r}; actual={actual!r}")


def run_suite(policy_path: Path, suite_path: Path) -> list[PolicyTestResult]:
    engine = PolicyEngine(policy_path)
    suite = load_suite(suite_path)
    results: list[PolicyTestResult] = []
    for case in suite.cases:
        evaluation = engine.evaluate(
            context=case.input.context,
            detected_sensitivity=case.input.detected_sensitivity,
            candidate_backend=case.input.candidate_backend,
        )
        expected = case.expect
        failures: list[str] = []
        scalar_checks = {
            "route": (evaluation.route, expected.route),
            "denied": (evaluation.denied, expected.denied),
            "transform": (evaluation.transform, expected.transform),
            "output_action": (evaluation.output_action, expected.output_action),
            "retention_days": (evaluation.retention_days, expected.retention_days),
            "matched_rules": (evaluation.matched_rules, expected.matched_rules),
            "allowed_backends": (evaluation.allowed_backends, expected.allowed_backends),
        }
        for label, (actual, wanted) in scalar_checks.items():
            if wanted is not None and actual != wanted:
                failures.append(f"{label}: expected {wanted!r}, got {actual!r}")
        _contains(
            "matched_rules",
            evaluation.matched_rules,
            expected.matched_rules_contain,
            failures,
        )
        _contains(
            "denied_tools",
            evaluation.denied_tools,
            expected.denied_tools_contain,
            failures,
        )
        _contains(
            "require_approval_for_tools",
            evaluation.require_approval_for_tools,
            expected.approval_tools_contain,
            failures,
        )
        results.append(PolicyTestResult(name=case.name, passed=not failures, failures=failures))
    return results
