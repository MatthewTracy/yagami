"""Read + write `config/yagami.toml` from the browser.

GET returns the current config plus a `defaults` snapshot so the UI can
show "reset to default" affordances. Secrets are never in this file (they
live in the OS keyring) - there is nothing to redact, but if the config
schema ever grows a secret-like field, redact it here before returning.

PUT accepts a partial JSON patch (any subset of YagamiConfig fields).
Validates against the full pydantic schema after merging. On success,
writes the file, invalidates the get_config LRU cache, pushes the new
effective RoutingConfig into the live RoutingPolicy (see set_policy /
RoutingPolicy.update_config), and returns the new config. routing.* changes
(default backend, spend cap, threshold, active profile) apply on the very
next turn, no restart. Backend model/URL/API-key changes still need a
process restart - those backends were constructed once at boot.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from ..config import YagamiConfig, effective_routing, get_config, write_config
from ..router.policy import RoutingPolicy
from ..router.prompts import PHI_MEDICAL_SYSTEM_PROMPT

router = APIRouter(prefix="/api/config", tags=["config"])

# Set once at app startup (main.build_app) via set_policy() - same pattern as
# sessions_api.set_store(). Lets a live PUT /api/config (including a profile
# switch) take effect on the policy's next decide() call without a restart.
_policy: RoutingPolicy | None = None


def set_policy(policy: RoutingPolicy) -> None:
    global _policy
    _policy = policy


def _config_payload() -> dict:
    cfg = get_config()
    return {
        "config": cfg.model_dump(mode="json"),
        "defaults": YagamiConfig().model_dump(mode="json"),
        "prompts": {
            "phi_medical_default": PHI_MEDICAL_SYSTEM_PROMPT,
        },
        "notes": {
            "phi_must_be_local": (
                "Locked on. Disabling would let PHI prompts reach cloud "
                "backends - defeats the local-first guarantee."
            ),
            "live_reload": (
                "Routing settings (default_backend, daily_spend_cap_usd, "
                "long_message_token_threshold, active_profile) take effect on "
                "the next turn. Backend model name / URL changes require "
                "restarting uvicorn."
            ),
        },
    }


@router.get("")
async def get_config_endpoint() -> dict:
    return _config_payload()


class ConfigPatch(BaseModel):
    """Loose patch shape - any field is optional. The server merges and
    re-validates against the full YagamiConfig pydantic model before
    persisting.

    `profiles` is a shallow merge like every other section: PUT-ing
    `{"profiles": {"work": {...}}}` replaces the *entire* "work" entry, it
    doesn't deep-merge individual override fields into an existing one. Send
    the full profile body each time.
    """

    ollama: dict | None = None
    anthropic: dict | None = None
    stability: dict | None = None
    openai: dict | None = None
    mistral: dict | None = None
    groq: dict | None = None
    openrouter: dict | None = None
    gemini: dict | None = None
    routing: dict | None = None
    profiles: dict[str, dict] | None = None


@router.put("")
async def put_config(patch: ConfigPatch) -> dict:
    cfg = get_config()
    merged = cfg.model_dump(mode="json")
    for section, body in patch.model_dump(exclude_none=True).items():
        merged.setdefault(section, {}).update(body)

    # Always pin phi_must_be_local on - defense in depth in case the UI
    # somehow PUTs it false despite the locked toggle. No profile can touch
    # this either - see ProfileOverrides.
    merged.setdefault("routing", {})["phi_must_be_local"] = True

    try:
        new_cfg = YagamiConfig.model_validate(merged)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors())

    path = write_config(new_cfg)
    if _policy is not None:
        _policy.update_config(effective_routing(new_cfg))
    return {"ok": True, "path": str(path), **_config_payload()}
