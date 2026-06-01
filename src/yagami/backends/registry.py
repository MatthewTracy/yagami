"""Backend discovery + construction.

Each backend module under `yagami.backends.` declares a `BUILDER` constant
that the registry calls with (config, secrets-getter) to build the backend
instance — or returns None if the backend isn't configured (missing API
key, missing model file, etc.). main.py loops over the registry instead
of hardcoding instantiations.

Adding a new backend is two files:
1. `yagami/backends/<name>.py` — implement the Backend protocol + a
    `build(cfg, secrets_get) -> Backend | None` function.
2. `config.py` — add a TOML section if the backend needs config.

No edit to main.py or this registry needed.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Callable, Protocol

from ..config import YagamiConfig
from .base import Backend

log = logging.getLogger("yagami.backends")


class SecretsGetter(Protocol):
    def __call__(self, name: str) -> str | None: ...


BackendBuilder = Callable[[YagamiConfig, SecretsGetter], "Backend | None"]


# Modules in `yagami.backends.` that aren't backends and should be skipped
# by discovery.
_NON_BACKEND_MODULES = {"base", "registry", "retry"}


def discover_builders() -> dict[str, BackendBuilder]:
    """Find every module under yagami.backends and pick up its `build` fn.

    Modules without a `build` function are silently ignored — they're either
    helpers (base, registry, retry) or in-progress.
    """
    import yagami.backends as pkg

    out: dict[str, BackendBuilder] = {}
    for _, mod_name, _ispkg in pkgutil.iter_modules(pkg.__path__):
        if mod_name in _NON_BACKEND_MODULES:
            continue
        try:
            mod = importlib.import_module(f"yagami.backends.{mod_name}")
        except ImportError as exc:
            log.warning("backend %s failed to import (%s); skipping", mod_name, exc)
            continue
        builder = getattr(mod, "build", None)
        if not callable(builder):
            continue
        out[mod_name] = builder
    return out


def build_all(cfg: YagamiConfig, secrets_get: SecretsGetter) -> dict[str, Backend]:
    """Discover + instantiate every available backend.

    Each builder may return None to indicate "configured to be off" (e.g. no
    API key). Builders that raise are logged + skipped — never crash startup
    over one broken backend.
    """
    builders = discover_builders()
    out: dict[str, Backend] = {}
    for mod_name, builder in builders.items():
        try:
            b = builder(cfg, secrets_get)
        except Exception as exc:  # noqa: BLE001 — never let a backend crash boot
            log.warning("backend %s build() raised %s; skipping", mod_name, exc)
            continue
        if b is None:
            continue
        # Don't trust the module name — use the backend's own .name as the
        # registry key. Lets us have e.g. multiple anthropic instances later.
        out[b.name] = b
    return out
