"""Programmatic Alembic entry point bundled with the Python package."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from .db import _normalize_database_url


def upgrade_database(database_url: str, *, revision: str = "head") -> None:
    if not database_url:
        raise ValueError("database URL is required")
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("alembic")))
    config.set_main_option(
        "sqlalchemy.url",
        _normalize_database_url(database_url, Path("yagami.db")),
    )
    config.attributes["programmatic_url"] = True
    command.upgrade(config, revision)
