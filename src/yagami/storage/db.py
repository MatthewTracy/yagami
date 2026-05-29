from __future__ import annotations

import logging
import time
from pathlib import Path

import aiosqlite

log = logging.getLogger("yagami.storage")

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_db: aiosqlite.Connection | None = None
_db_path: Path | None = None


def now_ms() -> int:
    return int(time.time() * 1000)


async def open_db(path: Path) -> aiosqlite.Connection:
    global _db, _db_path
    if _db is not None and _db_path == path:
        return _db
    if _db is not None:
        await _db.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(path))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.commit()
    await _run_migrations(conn)
    _db = conn
    _db_path = path
    return conn


async def close_db() -> None:
    global _db, _db_path
    if _db is not None:
        await _db.close()
        _db = None
        _db_path = None


def get_db() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("DB not opened; call open_db() first")
    return _db


async def _run_migrations(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)"
    )
    applied = {
        row[0]
        async for row in await conn.execute("SELECT version FROM schema_migrations")
    }
    for sql_path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        version = sql_path.stem
        if version in applied:
            continue
        log.info("applying migration %s", version)
        await conn.executescript(sql_path.read_text(encoding="utf-8"))
        await conn.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES(?, ?)",
            (version, now_ms()),
        )
        await conn.commit()
