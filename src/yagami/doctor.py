from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import httpx

from .config import get_config, get_settings


def _line(label: str, ok: bool, detail: str = "") -> str:
    mark = "OK  " if ok else "FAIL"
    return f"[{mark}] {label}" + (f"  ({detail})" if detail else "")


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
    print(_line("python >= 3.11", sys.version_info >= (3, 11), sys.version.split()[0]))

    ok, detail = await _check_ollama(cfg.ollama.url, cfg.ollama.model)
    print(
        _line(
            f"Ollama @ {cfg.ollama.url}",
            ok,
            f"trust zone: {cfg.ollama.trust_zone}; {detail}",
        )
    )

    if cfg.foundry_local.enabled:
        ok, detail = await _check_foundry_local(
            cfg.foundry_local.base_url,
            cfg.foundry_local.model,
        )
        print(_line(f"Foundry Local @ {cfg.foundry_local.base_url}", ok, detail))

    print(_line("ANTHROPIC_API_KEY set", bool(settings.anthropic_api_key)))
    print(_line("STABILITY_API_KEY set (optional)", bool(settings.stability_api_key)))

    config_path = Path(settings.config_path)
    print(_line(f"config file {config_path}", config_path.exists()))

    ollama_dir = Path.home() / ".ollama" / "models"
    probe = ollama_dir if ollama_dir.exists() else Path.home()
    free_gb = shutil.disk_usage(probe).free / 1024 / 1024 / 1024
    print(_line("Ollama model dir disk space", free_gb > 5, f"{free_gb:.1f} GB free at {probe}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
