"""Filesystem discovery for skills. Same pattern as backends/registry.py."""

from __future__ import annotations

import importlib
import logging
import pkgutil

from .base import Skill

log = logging.getLogger("yagami.skills")

_NON_SKILL_MODULES = {"base", "registry", "adapters"}


def discover_skills() -> dict[str, Skill]:
    """Find every module under yagami.skills/ that exposes build() -> Skill."""
    import yagami.skills as pkg

    out: dict[str, Skill] = {}
    for _, mod_name, _ispkg in pkgutil.iter_modules(pkg.__path__):
        if mod_name in _NON_SKILL_MODULES:
            continue
        try:
            mod = importlib.import_module(f"yagami.skills.{mod_name}")
        except ImportError as exc:
            log.warning("skill %s failed to import (%s); skipping", mod_name, exc)
            continue
        builder = getattr(mod, "build", None)
        if not callable(builder):
            continue
        try:
            skill = builder()
        except Exception as exc:  # noqa: BLE001
            log.warning("skill %s build() raised %s; skipping", mod_name, exc)
            continue
        out[skill.name] = skill
    return out
