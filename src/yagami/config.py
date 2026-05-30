from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

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


class RoutingConfig(BaseModel):
    long_message_token_threshold: int = 1500
    phi_must_be_local: bool = True
    default_backend: str = "ollama"
    lora_variants: dict[str, str] = Field(default_factory=dict)


class YagamiConfig(BaseModel):
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    anthropic: AnthropicConfig = Field(default_factory=AnthropicConfig)
    stability: StabilityConfig = Field(default_factory=StabilityConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)


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
