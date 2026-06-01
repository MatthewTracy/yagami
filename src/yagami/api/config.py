"""Read + write `config/yagami.toml` from the browser.

GET returns the current config plus a `defaults` snapshot so the UI can
show "reset to default" affordances. Secrets are never in this file (they
live in the OS keyring) — there is nothing to redact, but if the config
schema ever grows a secret-like field, redact it here before returning.

PUT accepts a partial JSON patch (any subset of YagamiConfig fields).
Validates against the full pydantic schema after merging. On success,
writes the file, invalidates the get_config LRU cache, and returns the
new config. Live FastAPI does NOT auto-reload the backends — the next
turn picks up routing.* settings; model URL / API key changes need a
process restart.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from ..config import YagamiConfig, get_config, write_config
from ..router.prompts import PHI_MEDICAL_SYSTEM_PROMPT

router = APIRouter(prefix="/api/config", tags=["config"])


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
                "backends — defeats the local-first guarantee."
            ),
            "live_reload": (
                "Routing settings (default_backend, daily_spend_cap_usd, "
                "long_message_token_threshold) take effect on the next turn. "
                "Model name / URL changes require restarting uvicorn."
            ),
        },
    }


@router.get("")
async def get_config_endpoint() -> dict:
    return _config_payload()


class ConfigPatch(BaseModel):
    """Loose patch shape — any field is optional. The server merges and
    re-validates against the full YagamiConfig pydantic model before
    persisting."""

    ollama: dict | None = None
    anthropic: dict | None = None
    stability: dict | None = None
    routing: dict | None = None


@router.put("")
async def put_config(patch: ConfigPatch) -> dict:
    cfg = get_config()
    merged = cfg.model_dump(mode="json")
    for section, body in patch.model_dump(exclude_none=True).items():
        merged.setdefault(section, {}).update(body)

    # Always pin phi_must_be_local on — defense in depth in case the UI
    # somehow PUTs it false despite the locked toggle.
    merged.setdefault("routing", {})["phi_must_be_local"] = True

    try:
        new_cfg = YagamiConfig.model_validate(merged)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors())

    path = write_config(new_cfg)
    return {"ok": True, "path": str(path), **_config_payload()}
