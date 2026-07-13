"""Filesystem discovery for skills. Same pattern as backends/registry.py."""

from __future__ import annotations

import importlib
import logging
import pkgutil

from .base import Skill
from .mcp_manager import get_manager as _get_mcp_manager

log = logging.getLogger("yagami.skills")

_NON_SKILL_MODULES = {"base", "registry", "adapters", "mcp_manager"}


def discover_skills() -> dict[str, Skill]:
    """Find every module under yagami.skills/ that exposes build() -> Skill,
    plus any tools exposed by connected MCP servers (see mcp_manager.py) -
    those aren't filesystem modules, they're discovered live from whatever
    servers are configured and connected."""
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

    mcp_manager = _get_mcp_manager()
    if mcp_manager is not None:
        out.update(mcp_manager.get_skills())
    return out
