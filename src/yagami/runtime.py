from __future__ import annotations

from dataclasses import dataclass

from .auth import Authenticator
from .backends.base import Backend
from .chat.session import SessionStore
from .config import Settings, YagamiConfig
from .gateway import GatewayService
from .governance import ApprovalStore, PrivacyTransformer, ToolSchemaRegistry
from .policy import PolicyEngine
from .projects import ProjectGovernor, ProjectRegistry
from .router.policy import RoutingPolicy
from .telemetry.observability import GatewayMetrics
from .telemetry.audit import AuditLedger


@dataclass
class AppRuntime:
    settings: Settings
    config: YagamiConfig
    backends: dict[str, Backend]
    routing_policy: RoutingPolicy
    policy_engine: PolicyEngine
    sessions: SessionStore
    authenticator: Authenticator
    metrics: GatewayMetrics
    transformer: PrivacyTransformer
    approvals: ApprovalStore
    tool_schemas: ToolSchemaRegistry
    projects: ProjectRegistry
    governor: ProjectGovernor
    audit: AuditLedger
    gateway: GatewayService
