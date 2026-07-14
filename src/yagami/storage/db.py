from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
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
        _db = None
        _db_path = None
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(path))
    try:
        conn.row_factory = aiosqlite.Row
        # Load sqlite-vec extension before migrations - they create vec0 virtual
        # tables. Silently no-op if the package isn't installed (memory features
        # degrade to FTS5 / disabled).
        await _load_sqlite_vec(conn)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.commit()
        await _run_migrations(conn)
    except BaseException:
        await conn.close()
        raise
    _db = conn
    _db_path = path
    return conn


async def _load_sqlite_vec(conn: aiosqlite.Connection) -> None:
    """Best-effort load of the sqlite-vec extension.

    aiosqlite runs the underlying sqlite3.Connection on its own worker
    thread; we must call into it via aiosqlite's async wrappers (or
    `_execute`) - touching `._conn` from another thread raises
    "SQLite objects created in a thread can only be used in that same thread".
    """
    try:
        import sqlite_vec  # type: ignore[import-not-found]
    except ImportError:
        log.warning("sqlite-vec not installed; vector memory disabled")
        return
    try:
        await conn.enable_load_extension(True)
        await conn.load_extension(sqlite_vec.loadable_path())
        await conn.enable_load_extension(False)
    except Exception as exc:  # noqa: BLE001 - extension load can fail many ways
        log.warning("failed to load sqlite-vec: %s; vector memory disabled", exc)


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


@asynccontextmanager
async def exclusive_db() -> AsyncIterator[aiosqlite.Connection]:
    """Open a separate write connection and hold an exclusive app transaction.

    Destructive lifecycle operations use this instead of the shared async
    connection so a chat append or embedding write cannot interleave between
    their related DELETE statements.
    """
    if _db_path is None:
        raise RuntimeError("DB not opened; call open_db() first")
    conn = await aiosqlite.connect(str(_db_path))
    try:
        conn.row_factory = aiosqlite.Row
        await _load_sqlite_vec(conn)
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            await conn.rollback()
            raise
        else:
            await conn.commit()
    finally:
        await conn.close()


@asynccontextmanager
async def snapshot_db() -> AsyncIterator[aiosqlite.Connection]:
    """Open a consistent read snapshot suitable for a streamed export."""
    if _db_path is None:
        raise RuntimeError("DB not opened; call open_db() first")
    conn = await aiosqlite.connect(str(_db_path))
    try:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("BEGIN")
        try:
            yield conn
        finally:
            await conn.rollback()
    finally:
        await conn.close()


async def _run_migrations(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)"
    )
    applied = {row[0] async for row in await conn.execute("SELECT version FROM schema_migrations")}
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
