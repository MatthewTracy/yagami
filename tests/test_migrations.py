from __future__ import annotations

import asyncio
import sqlite3

import pytest

pytest.importorskip("alembic")

from yagami.storage.migrate import upgrade_database


@pytest.mark.asyncio
async def test_packaged_alembic_migrates_clean_sqlite_to_head_idempotently(tmp_path) -> None:
    path = tmp_path / "alembic-clean.db"
    url = f"sqlite+aiosqlite:///{path.as_posix()}"

    await asyncio.to_thread(upgrade_database, url)
    await asyncio.to_thread(upgrade_database, url)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "20260727_0002",
        )
        versions = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
        assert "001_init" in versions
        assert "019_transform_envelope_keys" in versions
        assert "020_mcp_user_oauth" in versions
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='mcp_oauth_credentials'"
            ).fetchone()
            is not None
        )
    finally:
        connection.close()
