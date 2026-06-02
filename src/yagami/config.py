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


class MemoryConfig(BaseModel):
    enabled: bool = True
    embedding_model: str = "all-minilm"  # Ollama model name (384 dim)


class YagamiConfig(BaseModel):
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    anthropic: AnthropicConfig = Field(default_factory=AnthropicConfig)
    stability: StabilityConfig = Field(default_factory=StabilityConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    llama_cpp: LlamaCppConfig = Field(default_factory=LlamaCppConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)


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
    has no writer. Adding a dep just for one round-trip isn't worth it —
    the YagamiConfig shape is fixed and small.
    """
    out: list[str] = []
    data = cfg.model_dump(mode="json")
    for section, body in data.items():
        if not isinstance(body, dict):
            continue
        out.append(f"[{section}]")
        for key, val in body.items():
            if isinstance(val, dict):
                # Nested table — emit as `[section.key]` block of its own.
                continue
            out.append(f"{key} = {_format_toml_value(val)}")
        out.append("")
        # Handle nested-dict children after the parent block.
        for key, val in body.items():
            if isinstance(val, dict) and val:
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
