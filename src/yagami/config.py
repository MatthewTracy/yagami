from __future__ import annotations

import ipaddress
import re
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import AliasChoices, BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class OllamaConfig(BaseModel):
    url: str = "http://localhost:11434"
    model: str = "llama3.2:3b-instruct-q4_K_M"
    classifier_model: str = "llama3.2:3b-instruct-q4_K_M"


class AnthropicConfig(BaseModel):
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 4096


class StabilityConfig(BaseModel):
    model: str = "stable-image-core"


class OpenAIConfig(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4.1-mini"
    max_tokens: int = 4096


class UpstreamConfig(BaseModel):
    """Policy-only forwarding to an existing OpenAI-compatible gateway."""

    enabled: bool = False
    base_url: str = "http://localhost:4000/v1"
    model: str = ""
    max_tokens: int = 4096
    api_key_env: str = "UPSTREAM_API_KEY"
    allow_unauthenticated: bool = False


class FoundryLocalConfig(BaseModel):
    """Microsoft Foundry Local's loopback OpenAI-compatible service.

    The loopback restriction is a security boundary: routing policy treats
    this backend as local, so accepting a remote host here could silently
    bypass PHI and zero-cloud protections. Use ``upstream`` for a remote or
    network-hosted OpenAI-compatible service instead.
    """

    enabled: bool = False
    base_url: str = ""
    model: str = "qwen2.5-0.5b-instruct-generic-cpu"
    max_tokens: int = Field(default=4096, ge=1)

    @model_validator(mode="after")
    def validate_local_service(self) -> "FoundryLocalConfig":
        if self.enabled and not self.base_url:
            raise ValueError("enabled Foundry Local requires base_url")
        if self.enabled and not self.model.strip():
            raise ValueError("enabled Foundry Local requires model")
        if not self.base_url:
            return self

        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Foundry Local base_url must be an HTTP(S) loopback URL")
        if parsed.port == 0:
            raise ValueError("Foundry Local base_url requires a valid nonzero port")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "Foundry Local base_url cannot contain credentials, query, or fragment"
            )

        host = parsed.hostname.rstrip(".").lower()
        is_loopback = host == "localhost"
        if not is_loopback:
            try:
                is_loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                is_loopback = False
        if not is_loopback:
            raise ValueError(
                "Foundry Local must use localhost or a loopback IP; use upstream for remote hosts"
            )
        if parsed.path.rstrip("/") not in {"", "/v1"}:
            raise ValueError("Foundry Local base_url path must be empty or /v1")
        return self


class MistralConfig(BaseModel):
    model: str = "mistral-large-latest"
    max_tokens: int = 4096


class GroqConfig(BaseModel):
    model: str = "llama-3.3-70b-versatile"
    max_tokens: int = 4096


class OpenRouterConfig(BaseModel):
    # OpenRouter model ids are "vendor/model" - this default is a cheap,
    # widely-available one. Override freely; OpenRouter's whole pitch is
    # routing to whatever you name here.
    model: str = "openai/gpt-4o-mini"
    max_tokens: int = 4096


class GeminiConfig(BaseModel):
    model: str = "gemini-2.5-flash"
    max_tokens: int = 8192


class LlamaCppConfig(BaseModel):
    model_path: str = ""  # absolute path to a GGUF file; empty = disabled
    n_ctx: int = 8192
    n_gpu_layers: int = -1  # -1 = all on GPU if CUDA build, else CPU
    name: str = "llama_cpp"

    model_config = {"protected_namespaces": ()}


class RoutingConfig(BaseModel):
    long_message_token_threshold: int = Field(default=1500, ge=1)
    phi_must_be_local: bool = True
    default_backend: str = "ollama"
    lora_variants: dict[str, str] = Field(default_factory=dict)
    # Optional per-sensitivity local model choices. Phi-4 Mini follows
    # authorized administrative PHI instructions more reliably than the
    # small general generator while remaining entirely on-device.
    local_model_overrides: dict[str, str] = Field(default_factory=lambda: {"phi": "phi4-mini"})
    # 0 means no cap; positive values block cloud routes after the cap.
    daily_spend_cap_usd: float = Field(default=5.0, ge=0, allow_inf_nan=False)
    # Refuse ALL cloud routes, unconditionally - the "zero cloud" switch.
    # Distinct from daily_spend_cap_usd=0, which means NO cap (a trap that
    # used to be mis-documented as "no cloud spend").
    block_cloud: bool = False
    # A classifier outage must never turn into accidental cloud egress.
    # Automatic routes fall back to local and explicit cloud routes are refused.
    fail_closed_on_classifier_error: bool = True
    # "" = no profile active, [routing] above applies directly. Otherwise a
    # key into YagamiConfig.profiles - see ProfileOverrides.
    active_profile: str = ""


class ProfileOverrides(BaseModel):
    """Fields a named profile may override on top of [routing].

    `phi_must_be_local` is deliberately NOT overridable here - it's a hard
    invariant (see api/config.py put_config, which force-pins it true on
    every write), not a per-profile choice. A "personal" profile can relax
    the spend cap or change the default backend; it can never let PHI reach
    a cloud backend. `block_cloud` IS overridable in both directions -
    turning it ON per-profile is the whole point of a strict work profile,
    and turning it OFF is no more permissive than the [routing] default
    already allows.
    """

    daily_spend_cap_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    default_backend: str | None = None
    long_message_token_threshold: int | None = Field(default=None, ge=1)
    block_cloud: bool | None = None


class MemoryConfig(BaseModel):
    enabled: bool = True
    embedding_model: str = "all-minilm"  # Ollama model name (384 dim)


class PrivacyConfig(BaseModel):
    # Zero preserves conversations until the user deletes them. Positive
    # values remove sessions (and their derived cross-session memories) once
    # they have not been updated for this many days.
    session_retention_days: int = Field(default=0, ge=0, le=3650)


class McpServerConfig(BaseModel):
    """External MCP server over local stdio or remote Streamable HTTP.

    Stdio `env` is merged into the administrator-installed subprocess.
    Remote credentials are read only from the named environment variables and
    are never inherited from an inbound Yagami bearer token.
    """

    transport: Literal["stdio", "streamable_http"] = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    auth: Literal["none", "bearer_env", "client_credentials"] = "none"
    bearer_token_env: str = ""
    oauth_token_url: str = ""
    oauth_client_id_env: str = ""
    oauth_client_secret_env: str = ""
    oauth_scopes: list[str] = Field(default_factory=list)
    oauth_resource: str = ""
    oauth_token_endpoint_auth_method: Literal["client_secret_basic", "client_secret_post"] = (
        "client_secret_basic"  # noqa: S105 - OAuth method name, not a credential
    )

    @model_validator(mode="after")
    def validate_transport(self) -> "McpServerConfig":
        if self.transport == "stdio":
            if not self.command:
                raise ValueError("stdio MCP servers require command")
            return self
        if not self.url:
            raise ValueError("streamable_http MCP servers require url")
        if self.auth == "bearer_env" and not self.bearer_token_env:
            raise ValueError("bearer_env MCP auth requires bearer_token_env")
        if self.auth == "client_credentials":
            required = {
                "oauth_token_url": self.oauth_token_url,
                "oauth_client_id_env": self.oauth_client_id_env,
                "oauth_client_secret_env": self.oauth_client_secret_env,
                "oauth_resource": self.oauth_resource,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(
                    "client_credentials MCP auth missing " + ", ".join(sorted(missing))
                )
        return self


class YagamiConfig(BaseModel):
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    anthropic: AnthropicConfig = Field(default_factory=AnthropicConfig)
    stability: StabilityConfig = Field(default_factory=StabilityConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    upstream: UpstreamConfig = Field(default_factory=UpstreamConfig)
    foundry_local: FoundryLocalConfig = Field(default_factory=FoundryLocalConfig)
    mistral: MistralConfig = Field(default_factory=MistralConfig)
    groq: GroqConfig = Field(default_factory=GroqConfig)
    openrouter: OpenRouterConfig = Field(default_factory=OpenRouterConfig)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    llama_cpp: LlamaCppConfig = Field(default_factory=LlamaCppConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    profiles: dict[str, ProfileOverrides] = Field(default_factory=dict)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    mcp_servers: dict[str, McpServerConfig] = Field(default_factory=dict)


def effective_routing(cfg: YagamiConfig) -> RoutingConfig:
    """The RoutingConfig actually in effect: [routing] with the active
    profile's overrides (if any) applied on top. `phi_must_be_local` always
    comes from [routing] itself - see ProfileOverrides."""
    base = cfg.routing.model_copy(deep=True)
    profile = cfg.profiles.get(base.active_profile) if base.active_profile else None
    if profile is None:
        return base
    if profile.daily_spend_cap_usd is not None:
        base.daily_spend_cap_usd = profile.daily_spend_cap_usd
    if profile.default_backend is not None:
        base.default_backend = profile.default_backend
    if profile.long_message_token_threshold is not None:
        base.long_message_token_threshold = profile.long_message_token_threshold
    if profile.block_cloud is not None:
        base.block_cloud = profile.block_cloud
    return base


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    anthropic_api_key: str = Field(default="", validation_alias=AliasChoices("ANTHROPIC_API_KEY"))
    stability_api_key: str = Field(default="", validation_alias=AliasChoices("STABILITY_API_KEY"))
    ollama_url: str = Field(default="", validation_alias=AliasChoices("YAGAMI_OLLAMA_URL"))
    ollama_model: str = Field(default="", validation_alias=AliasChoices("YAGAMI_OLLAMA_MODEL"))
    claude_model: str = Field(default="", validation_alias=AliasChoices("YAGAMI_CLAUDE_MODEL"))
    config_path: str = Field(
        default="config/yagami.toml", validation_alias=AliasChoices("YAGAMI_CONFIG_PATH")
    )
    db_path: str = Field(default="yagami.db", validation_alias=AliasChoices("YAGAMI_DB_PATH"))
    policy_path: str = Field(
        default="config/policy.yaml", validation_alias=AliasChoices("YAGAMI_POLICY_PATH")
    )
    projects_path: str = Field(
        default="config/projects.yaml", validation_alias=AliasChoices("YAGAMI_PROJECTS_PATH")
    )
    kb_roots: str = Field(default="", validation_alias=AliasChoices("YAGAMI_KB_ROOTS"))
    api_keys: str = Field(default="", validation_alias=AliasChoices("YAGAMI_API_KEYS"))
    require_auth: bool = Field(default=False, validation_alias=AliasChoices("YAGAMI_REQUIRE_AUTH"))
    oidc_issuer: str = Field(default="", validation_alias=AliasChoices("YAGAMI_OIDC_ISSUER"))
    oidc_audience: str = Field(default="", validation_alias=AliasChoices("YAGAMI_OIDC_AUDIENCE"))
    oidc_jwks_url: str = Field(default="", validation_alias=AliasChoices("YAGAMI_OIDC_JWKS_URL"))
    oidc_project_claim: str = Field(
        default="yagami_project", validation_alias=AliasChoices("YAGAMI_OIDC_PROJECT_CLAIM")
    )
    oidc_roles_claim: str = Field(
        default="roles", validation_alias=AliasChoices("YAGAMI_OIDC_ROLES_CLAIM")
    )
    oidc_scopes_claim: str = Field(
        default="scope", validation_alias=AliasChoices("YAGAMI_OIDC_SCOPES_CLAIM")
    )
    headless: bool = Field(default=False, validation_alias=AliasChoices("YAGAMI_HEADLESS"))
    demo_mode: bool = Field(default=False, validation_alias=AliasChoices("YAGAMI_DEMO_MODE"))
    mcp_server_enabled: bool = Field(
        default=True, validation_alias=AliasChoices("YAGAMI_MCP_SERVER_ENABLED")
    )
    metrics_enabled: bool = Field(
        default=True, validation_alias=AliasChoices("YAGAMI_METRICS_ENABLED")
    )
    max_request_bytes: int = Field(
        default=32 * 1024 * 1024,
        ge=1_048_576,
        le=268_435_456,
        validation_alias=AliasChoices("YAGAMI_MAX_REQUEST_BYTES"),
    )
    transform_key: str = Field(default="", validation_alias=AliasChoices("YAGAMI_TRANSFORM_KEY"))
    transform_key_ref: str = Field(
        default="", validation_alias=AliasChoices("YAGAMI_TRANSFORM_KEY_REF")
    )
    transform_vault_ttl_seconds: int = Field(
        default=3600,
        ge=60,
        le=86_400,
        validation_alias=AliasChoices("YAGAMI_TRANSFORM_VAULT_TTL_SECONDS"),
    )
    audit_key: str = Field(default="", validation_alias=AliasChoices("YAGAMI_AUDIT_KEY"))
    audit_key_ref: str = Field(default="", validation_alias=AliasChoices("YAGAMI_AUDIT_KEY_REF"))
    audit_required: bool = Field(
        default=False, validation_alias=AliasChoices("YAGAMI_AUDIT_REQUIRED")
    )
    audit_sink_url: str = Field(default="", validation_alias=AliasChoices("YAGAMI_AUDIT_SINK_URL"))
    audit_sink_token: str = Field(
        default="", validation_alias=AliasChoices("YAGAMI_AUDIT_SINK_TOKEN")
    )
    audit_sink_token_ref: str = Field(
        default="", validation_alias=AliasChoices("YAGAMI_AUDIT_SINK_TOKEN_REF")
    )
    audit_sink_format: Literal["json", "splunk_hec"] = Field(
        default="json", validation_alias=AliasChoices("YAGAMI_AUDIT_SINK_FORMAT")
    )
    audit_sink_required: bool = Field(
        default=False, validation_alias=AliasChoices("YAGAMI_AUDIT_SINK_REQUIRED")
    )
    audit_sink_timeout_seconds: float = Field(
        default=5.0,
        ge=0.5,
        le=30.0,
        validation_alias=AliasChoices("YAGAMI_AUDIT_SINK_TIMEOUT_SECONDS"),
    )
    approval_webhook_url: str = Field(
        default="", validation_alias=AliasChoices("YAGAMI_APPROVAL_WEBHOOK_URL")
    )
    approval_webhook_format: Literal["json", "slack", "teams"] = Field(
        default="json", validation_alias=AliasChoices("YAGAMI_APPROVAL_WEBHOOK_FORMAT")
    )
    approval_webhook_timeout_seconds: float = Field(
        default=5.0,
        ge=0.5,
        le=30.0,
        validation_alias=AliasChoices("YAGAMI_APPROVAL_WEBHOOK_TIMEOUT_SECONDS"),
    )
    presidio_url: str = Field(default="", validation_alias=AliasChoices("YAGAMI_PRESIDIO_URL"))
    presidio_language: str = Field(
        default="en", validation_alias=AliasChoices("YAGAMI_PRESIDIO_LANGUAGE")
    )
    presidio_score_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices("YAGAMI_PRESIDIO_SCORE_THRESHOLD"),
    )
    presidio_timeout_seconds: float = Field(
        default=5.0,
        ge=0.5,
        le=30.0,
        validation_alias=AliasChoices("YAGAMI_PRESIDIO_TIMEOUT_SECONDS"),
    )
    presidio_fail_closed: bool = Field(
        default=True, validation_alias=AliasChoices("YAGAMI_PRESIDIO_FAIL_CLOSED")
    )
    presidio_allow_remote: bool = Field(
        default=False, validation_alias=AliasChoices("YAGAMI_PRESIDIO_ALLOW_REMOTE")
    )
    presidio_token: str = Field(default="", validation_alias=AliasChoices("YAGAMI_PRESIDIO_TOKEN"))
    presidio_token_ref: str = Field(
        default="", validation_alias=AliasChoices("YAGAMI_PRESIDIO_TOKEN_REF")
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_config() -> YagamiConfig:
    settings = get_settings()
    path = Path(settings.config_path)
    if not path.exists():
        return YagamiConfig()
    with path.open("rb") as f:
        data = tomllib.load(f)
    cfg = YagamiConfig.model_validate(data)
    if settings.ollama_url:
        cfg.ollama.url = settings.ollama_url
    if settings.ollama_model:
        cfg.ollama.model = settings.ollama_model
    if settings.claude_model:
        cfg.anthropic.model = settings.claude_model
    return cfg


def _format_toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        # TOML basic strings must escape quotes, backslashes, and controls.
        escapes = {
            '"': '\\"',
            "\\": "\\\\",
            "\b": "\\b",
            "\t": "\\t",
            "\n": "\\n",
            "\f": "\\f",
            "\r": "\\r",
        }
        encoded: list[str] = []
        for char in v:
            if char in escapes:
                encoded.append(escapes[char])
            elif ord(char) <= 0x1F or ord(char) == 0x7F:
                encoded.append(f"\\u{ord(char):04X}")
            else:
                encoded.append(char)
        return '"' + "".join(encoded) + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_format_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        # Inline table: { k = v, ... }
        return (
            "{"
            + ", ".join(
                f"{_format_toml_key(k)} = {_format_toml_value(val)}" for k, val in v.items()
            )
            + "}"
        )
    raise TypeError(f"unsupported TOML value type: {type(v).__name__}")


_BARE_TOML_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _format_toml_key(key: str) -> str:
    """Quote dynamic TOML keys that would otherwise change table nesting."""
    return key if _BARE_TOML_KEY.fullmatch(key) else _format_toml_value(key)


def _serialize_config(cfg: YagamiConfig) -> str:
    """Tiny hand-rolled TOML writer scoped to this config's shape.

    We use `tomli_w` indirectly via stdlib `tomllib` for reading; the stdlib
    has no writer. Adding a dep just for one round-trip isn't worth it -
    the YagamiConfig shape is fixed and small.

    `exclude_none=True` matters for ProfileOverrides - its fields default to
    None (unset), and TOML has no null literal to write one as.
    """
    out: list[str] = []
    data = cfg.model_dump(mode="json", exclude_none=True)
    for section, body in data.items():
        if not isinstance(body, dict):
            continue
        out.append(f"[{_format_toml_key(section)}]")
        for key, val in body.items():
            if isinstance(val, dict):
                # Nested table - emit as `[section.key]` block of its own.
                continue
            out.append(f"{_format_toml_key(key)} = {_format_toml_value(val)}")
        out.append("")
        # Handle nested-dict children after the parent block. Emit even when
        # empty (e.g. a profile with no overrides set yet) - dropping empty
        # tables would lose the key's existence entirely on the next read,
        # not just its (absent) contents.
        for key, val in body.items():
            if isinstance(val, dict):
                out.append(f"[{_format_toml_key(section)}.{_format_toml_key(key)}]")
                for k2, v2 in val.items():
                    out.append(f"{_format_toml_key(k2)} = {_format_toml_value(v2)}")
                out.append("")
    return "\n".join(out).rstrip() + "\n"


def write_config(cfg: YagamiConfig) -> Path:
    """Persist YagamiConfig back to disk at the path get_settings().config_path
    points at. Returns the resolved path. Also invalidates the get_config
    LRU cache so the next call reads the new file.
    """
    path = Path(get_settings().config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Replace atomically from the same directory so a crash or power loss
    # cannot leave a half-written config file behind.
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        tmp_path.write_text(_serialize_config(cfg), encoding="utf-8")
        tmp_path.replace(path)
    finally:
        tmp_path.unlink(missing_ok=True)
    get_config.cache_clear()
    return path
