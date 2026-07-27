from __future__ import annotations

from dataclasses import dataclass

from .auth import Authenticator
from .backends.base import Backend
from .chat.session import SessionStore
from .config import Settings, YagamiConfig
from .coordination import Coordinator
from .gateway import GatewayService
from .governance import ApprovalStore, PrivacyTransformer, ToolSchemaRegistry
from .memory.embedder import EmbedderProtocol
from .policy import PolicyEngine
from .projects import ProjectGovernor, ProjectRegistry
from .router.policy import RoutingPolicy
from .telemetry.observability import GatewayMetrics
from .telemetry.audit import AuditLedger
from .skills.mcp_oauth import OAuthCredentialStore


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
    coordinator: Coordinator
    audit: AuditLedger
    gateway: GatewayService
    embedder: EmbedderProtocol | None = None
    mcp_oauth: OAuthCredentialStore | None = None
    mcp_server: object | None = None
