from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..router.schema import Sensitivity


class PolicyMode(str, Enum):
    ENFORCE = "enforce"
    SHADOW = "shadow"


class RoutePolicy(str, Enum):
    AUTO = "auto"
    LOCAL = "local"
    CLOUD = "cloud"
    DENY = "deny"


class TransformPolicy(str, Enum):
    NONE = "none"
    REDACT = "redact"
    TOKENIZE = "tokenize"


class OutputPolicy(str, Enum):
    ALLOW = "allow"
    REDACT = "redact"
    BLOCK = "block"


class PolicyContext(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project_id: str = Field(min_length=1, max_length=64)
    subject_id: str | None = Field(default=None, max_length=128)
    purpose: str = Field(default="general", min_length=1, max_length=64)
    jurisdiction: str | None = Field(default=None, max_length=32)
    session_id: str | None = Field(default=None, max_length=128)
    sensitivity_hint: Sensitivity | None = None
    requested_tools: list[str] = Field(default_factory=list, max_length=100)
    approved_tools: list[str] = Field(default_factory=list, max_length=100)
    approval_tokens: list[str] = Field(default_factory=list, max_length=10, exclude=True)
    approval_ids: list[str] = Field(default_factory=list, max_length=10)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("requested_tools", "approved_tools", "approval_tokens", "approval_ids")
    @classmethod
    def unique_tools(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(tool.strip() for tool in value if tool.strip()))
        if any(len(tool) > 256 for tool in normalized):
            raise ValueError("tool and approval identifiers are limited to 256 characters")
        return normalized


class PolicyMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projects: list[str] = Field(default_factory=list)
    purposes: list[str] = Field(default_factory=list)
    sensitivities: list[Sensitivity] = Field(default_factory=list)
    jurisdictions: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)


class PolicyEffect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: RoutePolicy | None = None
    allowed_backends: list[str] | None = None
    denied_tools: list[str] = Field(default_factory=list)
    require_approval_for_tools: list[str] = Field(default_factory=list)
    transform: TransformPolicy | None = None
    output_action: OutputPolicy | None = None
    retention_days: int | None = Field(default=None, ge=0, le=3650)


class PolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    description: str = Field(default="", max_length=500)
    priority: int = Field(default=0, ge=-100_000, le=100_000)
    enabled: bool = True
    match: PolicyMatch = Field(default_factory=PolicyMatch)
    effect: PolicyEffect


class PolicyDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: RoutePolicy = RoutePolicy.AUTO
    allowed_backends: list[str] | None = None
    denied_tools: list[str] = Field(default_factory=list)
    require_approval_for_tools: list[str] = Field(default_factory=list)
    transform: TransformPolicy = TransformPolicy.NONE
    output_action: OutputPolicy = OutputPolicy.ALLOW
    retention_days: int = Field(default=30, ge=0, le=3650)


class PolicyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default="yagami-default", min_length=1, max_length=128)
    version: str = Field(default="1.0.0", min_length=1, max_length=64)
    mode: PolicyMode = PolicyMode.ENFORCE
    defaults: PolicyDefaults = Field(default_factory=PolicyDefaults)
    rules: list[PolicyRule] = Field(default_factory=list)

    @field_validator("rules")
    @classmethod
    def unique_rule_ids(cls, rules: list[PolicyRule]) -> list[PolicyRule]:
        ids = [rule.id for rule in rules]
        if len(ids) != len(set(ids)):
            raise ValueError("policy rule IDs must be unique")
        return rules


class PolicyEvaluation(BaseModel):
    policy_id: str
    policy_version: str
    policy_hash: str
    mode: PolicyMode
    matched_rules: list[str]
    detected_sensitivity: Sensitivity
    effective_sensitivity: Sensitivity
    route: RoutePolicy
    allowed_backends: list[str] | None
    denied_tools: list[str]
    require_approval_for_tools: list[str]
    transform: TransformPolicy
    output_action: OutputPolicy
    retention_days: int
    candidate_backend: str
    enforced_backend: str | None = None
    denied: bool = False
    reasons: list[str] = Field(default_factory=list)
    lineage: dict[str, Any] | None = None
    transformations: list[dict[str, Any]] = Field(default_factory=list)
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    output_inspection: dict[str, Any] | None = None

    def passport(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
