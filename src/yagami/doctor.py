from __future__ import annotations

import asyncio
import importlib.util
import shutil
import sys
from pathlib import Path

import httpx

from .config import get_config, get_settings


def _line(label: str, ok: bool | None, detail: str = "") -> str:
    mark = "SKIP" if ok is None else ("OK  " if ok else "FAIL")
    return f"[{mark}] {label}" + (f"  ({detail})" if detail else "")


def _next(command: str) -> None:
    print(f"       Next: {command}")


async def _check_ollama(url: str, model: str) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{url}/api/tags")
            r.raise_for_status()
            tags = {t["name"] for t in r.json().get("models", [])}
            return (model in tags, f"models loaded: {len(tags)}; default present: {model in tags}")
    except httpx.HTTPError as exc:
        return False, f"unreachable: {exc}"


async def _check_foundry_local(url: str, model: str) -> tuple[bool, str]:
    root = url.removesuffix("/").removesuffix("/v1")
    try:
        async with httpx.AsyncClient(base_url=root, timeout=5.0) as client:
            status = await client.get("/openai/status")
            status.raise_for_status()
            loaded = await client.get("/openai/loadedmodels")
            loaded.raise_for_status()
            payload = loaded.json()
            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict):
                items = payload.get("models", [])
            else:
                return False, "invalid loaded-models response"
            names = {
                item
                if isinstance(item, str)
                else item.get("model") or item.get("name") or item.get("id")
                for item in items
                if isinstance(item, (str, dict))
            }
            names.discard(None)
            present = model.casefold() in {str(name).casefold() for name in names}
            return (
                present,
                f"models loaded: {len(names)}; configured present: {present}",
            )
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        return False, f"unreachable or invalid response: {exc}"


async def main() -> int:
    cfg = get_config()
    settings = get_settings()
    python_ok = sys.version_info >= (3, 11)
    print(_line("python >= 3.11", python_ok, sys.version.split()[0]))
    if not python_ok:
        _next("install Python 3.11 or newer, then recreate the Yagami environment")

    ollama_ok, detail = await _check_ollama(cfg.ollama.url, cfg.ollama.model)
    print(
        _line(
            f"Ollama @ {cfg.ollama.url}",
            ollama_ok,
            f"trust zone: {cfg.ollama.trust_zone.value}; {detail}",
        )
    )

    optional_modules = {
        "OpenAI-compatible provider SDK": ("openai", "yagami[providers]"),
        "Anthropic provider SDK": ("anthropic", "yagami[providers]"),
        "PDF ingestion": ("pypdf", "yagami[ingest]"),
        "OS keyring": ("keyring", "yagami[desktop]"),
    }
    for label, (module, extra) in optional_modules.items():
        installed = importlib.util.find_spec(module) is not None
        print(
            _line(
                label,
                True if installed else None,
                "installed" if installed else f"optional; install `{extra}` to enable",
            )
        )
    if not ollama_ok:
        if detail.startswith("unreachable"):
            _next("start Ollama with `ollama serve`, then rerun `yagami doctor`")
        else:
            _next(f"install the configured model with `ollama pull {cfg.ollama.model}`")

    foundry_ok = False
    if cfg.foundry_local.enabled:
        foundry_ok, detail = await _check_foundry_local(
            cfg.foundry_local.base_url,
            cfg.foundry_local.model,
        )
        print(_line(f"Foundry Local @ {cfg.foundry_local.base_url}", foundry_ok, detail))
        if not foundry_ok:
            _next("run `foundry service status` and update [foundry_local].base_url/model")

    print(
        _line(
            "Anthropic cloud backend",
            True if settings.anthropic_api_key else None,
            "credential configured" if settings.anthropic_api_key else "optional; no key found",
        )
    )
    print(
        _line(
            "Stability image backend",
            True if settings.stability_api_key else None,
            "credential configured" if settings.stability_api_key else "optional; no key found",
        )
    )

    config_path = Path(settings.config_path)
    config_ok = config_path.exists()
    print(_line(f"config file {config_path}", config_ok))
    if not config_ok:
        _next("run `yagami init`, then `yagami doctor`")

    auth_ok = bool(settings.api_keys) or bool(settings.oidc_issuer and settings.oidc_jwks_url)
    if settings.headless or settings.require_auth:
        print(_line("headless gateway authentication", auth_ok))
        if not auth_ok:
            _next("set YAGAMI_API_KEYS, or configure both OIDC issuer and JWKS URL")

    ollama_dir = Path.home() / ".ollama" / "models"
    probe = ollama_dir if ollama_dir.exists() else Path.home()
    free_gb = shutil.disk_usage(probe).free / 1024 / 1024 / 1024
    disk_ok = free_gb > 5
    if disk_ok:
        print(_line("Ollama model dir disk space", True, f"{free_gb:.1f} GB free at {probe}"))
    else:
        print(f"[WARN] Ollama model dir disk space  ({free_gb:.1f} GB free at {probe})")
        _next("set OLLAMA_MODELS to a folder on a drive with more space before pulling models")

    if not any((ollama_ok, foundry_ok, bool(settings.anthropic_api_key))):
        print("[INFO] No verified generation backend is ready; `yagami demo` still works offline.")
    required_ok = python_ok and config_ok
    if settings.headless or settings.require_auth:
        required_ok = required_ok and auth_ok
    print("Doctor result: " + ("ready" if required_ok else "action required"))
    return 0 if required_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
