from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, Field
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
    long_message_token_threshold: int = 1500
    phi_must_be_local: bool = True
    default_backend: str = "ollama"
    lora_variants: dict[str, str] = Field(default_factory=dict)
    daily_spend_cap_usd: float = 5.0  # 0 = no cap; cloud routes refused over cap
    # Refuse ALL cloud routes, unconditionally - the "zero cloud" switch.
    # Distinct from daily_spend_cap_usd=0, which means NO cap (a trap that
    # used to be mis-documented as "no cloud spend").
    block_cloud: bool = False
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

    daily_spend_cap_usd: float | None = None
    default_backend: str | None = None
    long_message_token_threshold: int | None = None
    block_cloud: bool | None = None


class MemoryConfig(BaseModel):
    enabled: bool = True
    embedding_model: str = "all-minilm"  # Ollama model name (384 dim)


class McpServerConfig(BaseModel):
    """One external MCP server to connect to over stdio. `command` is
    launched as a subprocess (e.g. `npx`, `python`, `uvx`) with `args`;
    `env` is merged into that subprocess's environment (useful for API keys
    an MCP server itself needs - those are the MCP server's own secrets,
    not read from Yagami's keyring)."""

    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class YagamiConfig(BaseModel):
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    anthropic: AnthropicConfig = Field(default_factory=AnthropicConfig)
    stability: StabilityConfig = Field(default_factory=StabilityConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    mistral: MistralConfig = Field(default_factory=MistralConfig)
    groq: GroqConfig = Field(default_factory=GroqConfig)
    openrouter: OpenRouterConfig = Field(default_factory=OpenRouterConfig)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    llama_cpp: LlamaCppConfig = Field(default_factory=LlamaCppConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    profiles: dict[str, ProfileOverrides] = Field(default_factory=dict)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
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
        # Quote with double-quotes, escape backslashes + quotes.
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_format_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        # Inline table: { k = v, ... }
        return "{" + ", ".join(f"{k} = {_format_toml_value(val)}" for k, val in v.items()) + "}"
    raise TypeError(f"unsupported TOML value type: {type(v).__name__}")


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
        out.append(f"[{section}]")
        for key, val in body.items():
            if isinstance(val, dict):
                # Nested table - emit as `[section.key]` block of its own.
                continue
            out.append(f"{key} = {_format_toml_value(val)}")
        out.append("")
        # Handle nested-dict children after the parent block. Emit even when
        # empty (e.g. a profile with no overrides set yet) - dropping empty
        # tables would lose the key's existence entirely on the next read,
        # not just its (absent) contents.
        for key, val in body.items():
            if isinstance(val, dict):
                out.append(f"[{section}.{key}]")
                for k2, v2 in val.items():
                    out.append(f"{k2} = {_format_toml_value(v2)}")
                out.append("")
    return "\n".join(out).rstrip() + "\n"


def write_config(cfg: YagamiConfig) -> Path:
    """Persist YagamiConfig back to disk at the path get_settings().config_path
    points at. Returns the resolved path. Also invalidates the get_config
    LRU cache so the next call reads the new file.
    """
    path = Path(get_settings().config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialize_config(cfg), encoding="utf-8")
    get_config.cache_clear()
    return path
