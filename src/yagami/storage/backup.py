"""Offline backup and restore verification for Yagami state."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

_REQUIRED_TABLES = {
    "sessions",
    "decisions",
    "observations",
    "audit_events",
    "schema_migrations",
}


def verify_sqlite_backup(path: Path) -> dict[str, object]:
    backup = path.expanduser().resolve(strict=True)
    connection = sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)
    try:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = sorted(_REQUIRED_TABLES - tables)
        if quick_check != "ok" or missing:
            raise ValueError(
                f"backup verification failed: quick_check={quick_check!r}, missing={missing}"
            )
        migration_count = int(
            connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        )
        return {
            "ok": True,
            "format": "sqlite",
            "path": str(backup),
            "migrations": migration_count,
        }
    finally:
        connection.close()


def backup_sqlite(source: Path, output: Path, *, force: bool = False) -> dict[str, object]:
    source_path = source.expanduser().resolve(strict=True)
    output_path = output.expanduser().resolve()
    if source_path == output_path:
        raise ValueError("backup output must differ from the live database")
    if output_path.exists() and not force:
        raise FileExistsError(f"backup already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".partial")
    if temporary.exists():
        temporary.unlink()

    live = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True)
    destination = sqlite3.connect(temporary)
    try:
        if str(live.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
            raise ValueError("live SQLite database failed quick_check")
        live.backup(destination)
        destination.commit()
    finally:
        destination.close()
        live.close()
    temporary.replace(output_path)
    return verify_sqlite_backup(output_path)


def restore_sqlite_backup(
    backup: Path,
    target: Path,
    *,
    force: bool = False,
) -> dict[str, object]:
    backup_path = backup.expanduser().resolve(strict=True)
    verify_sqlite_backup(backup_path)
    target_path = target.expanduser().resolve()
    if backup_path == target_path:
        raise ValueError("restore target must differ from the backup")
    if target_path.exists() and not force:
        raise FileExistsError(f"restore target already exists: {target_path}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = target_path.with_name(target_path.name + ".restore-partial")
    if temporary.exists():
        temporary.unlink()
    shutil.copy2(backup_path, temporary)
    verify_sqlite_backup(temporary)
    temporary.replace(target_path)
    return verify_sqlite_backup(target_path)


def backup_postgresql(
    database_url: str,
    output: Path,
    *,
    force: bool = False,
) -> dict[str, object]:
    pg_dump = shutil.which("pg_dump")
    if pg_dump is None:
        raise RuntimeError("pg_dump is required for PostgreSQL backups")
    parsed = urlsplit(database_url.replace("postgresql+asyncpg://", "postgresql://", 1))
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise ValueError("PostgreSQL backup URL is invalid")
    database = unquote(parsed.path.lstrip("/"))
    if not database:
        raise ValueError("PostgreSQL backup URL must name a database")
    output_path = output.expanduser().resolve()
    if output_path.exists() and not force:
        raise FileExistsError(f"backup already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    if parsed.password:
        environment["PGPASSWORD"] = unquote(parsed.password)
    command = [
        pg_dump,
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--host",
        parsed.hostname,
        "--port",
        str(parsed.port or 5432),
        "--username",
        unquote(parsed.username or ""),
        "--dbname",
        database,
        "--file",
        str(output_path),
    ]
    subprocess.run(command, env=environment, check=True)  # noqa: S603 - fixed executable/args
    return verify_postgresql_backup(output_path)


def verify_postgresql_backup(path: Path) -> dict[str, object]:
    pg_restore = shutil.which("pg_restore")
    if pg_restore is None:
        raise RuntimeError("pg_restore is required to verify PostgreSQL backups")
    backup = path.expanduser().resolve(strict=True)
    result = subprocess.run(  # noqa: S603 - fixed executable/args
        [pg_restore, "--list", str(backup)],
        check=True,
        capture_output=True,
        text=True,
    )
    entries = sum(
        bool(line.strip()) and not line.startswith(";") for line in result.stdout.splitlines()
    )
    if entries == 0:
        raise ValueError("PostgreSQL backup contains no restorable entries")
    return {
        "ok": True,
        "format": "postgresql-custom",
        "path": str(backup),
        "entries": entries,
    }
