"""Runtime paths shared by source checkouts and installed wheels."""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    configured = os.getenv("YAGAMI_PROJECT_ROOT")
    return Path(configured).expanduser().resolve() if configured else Path(__file__).parents[2]


def default_state_dir() -> Path:
    configured = os.getenv("YAGAMI_HOME")
    return (
        Path(configured).expanduser().resolve()
        if configured
        else (Path.home() / ".yagami").resolve()
    )


def configure_default_state(state_dir: Path | None = None) -> Path:
    """Use initialized per-user state when no explicit/project config exists."""
    state = (state_dir or default_state_dir()).expanduser().resolve()
    project_config = Path.cwd() / "config" / "yagami.toml"
    state_config = state / "config" / "yagami.toml"
    if project_config.exists() and state_dir is None:
        return project_config.parent.parent
    if state_config.exists():
        defaults = {
            "YAGAMI_PROJECT_ROOT": str(state),
            "YAGAMI_CONFIG_PATH": str(state_config),
            "YAGAMI_POLICY_PATH": str(state / "config" / "policy.yaml"),
            "YAGAMI_PROJECTS_PATH": str(state / "config" / "projects.yaml"),
            "YAGAMI_DB_PATH": str(state / "data" / "yagami.db"),
        }
        for name, value in defaults.items():
            os.environ.setdefault(name, value)
    return state


def template_root() -> Path | None:
    source = project_root()
    if (source / "config" / "yagami.toml").exists():
        return source
    packaged = Path(__file__).parent / "templates"
    return packaged if (packaged / "config" / "yagami.toml").exists() else None


def ui_dist() -> Path | None:
    candidates = (
        project_root() / "ui" / "dist",
        Path(__file__).parent / "ui_dist",
    )
    return next(
        (candidate for candidate in candidates if (candidate / "index.html").exists()), None
    )
