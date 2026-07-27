from __future__ import annotations

from pathlib import Path

import pytest

from yagami.storage.backup import (
    backup_sqlite,
    restore_sqlite_backup,
    verify_sqlite_backup,
)


@pytest.mark.asyncio
async def test_sqlite_backup_and_restore_are_verified(fresh_db, tmp_path: Path) -> None:
    await fresh_db.execute(
        "INSERT INTO sessions(id, created_at, updated_at) VALUES('backup-session', 1, 1)"
    )
    await fresh_db.commit()
    source = tmp_path / "yagami_test.db"
    backup = tmp_path / "backups" / "yagami.db"
    restored = tmp_path / "restored" / "yagami.db"

    result = backup_sqlite(source, backup)
    assert result["ok"]
    assert int(result["migrations"]) >= 19
    assert verify_sqlite_backup(backup)["ok"]
    assert restore_sqlite_backup(backup, restored)["ok"]

    import sqlite3

    connection = sqlite3.connect(restored)
    try:
        assert connection.execute(
            "SELECT id FROM sessions WHERE id='backup-session'"
        ).fetchone() == ("backup-session",)
    finally:
        connection.close()


def test_backup_refuses_overwrite_and_invalid_source(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        backup_sqlite(missing, tmp_path / "backup.db")
