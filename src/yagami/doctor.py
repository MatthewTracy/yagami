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


async def main() -> int:
    cfg = get_config()
    settings = get_settings()
    print(_line("python >= 3.11", sys.version_info >= (3, 11), sys.version.split()[0]))

    ok, detail = await _check_ollama(cfg.ollama.url, cfg.ollama.model)
    print(_line(f"Ollama @ {cfg.ollama.url}", ok, detail))

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
