from __future__ import annotations

import logging
import time
import asyncio
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, overload

import aiosqlite
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

log = logging.getLogger("yagami.storage")

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_db: DatabaseConnection | None = None
_engine: AsyncEngine | None = None
_db_path: Path | None = None
_database_url: str = ""


def now_ms() -> int:
    return int(time.time() * 1000)


class DatabaseRow:
    """Row compatible with both aiosqlite and SQLAlchemy result consumers."""

    def __init__(self, keys: Sequence[str], values: Sequence[Any]) -> None:
        self._keys = tuple(keys)
        self._values = tuple(values)
        self._indexes = {key: index for index, key in enumerate(self._keys)}

    @overload
    def __getitem__(self, key: str) -> Any: ...

    @overload
    def __getitem__(self, key: int) -> Any: ...

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._indexes[key]]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._keys)

    def keys(self) -> tuple[str, ...]:
        return self._keys


class DatabaseCursor:
    """Buffered async cursor detached from the underlying shared connection."""

    def __init__(
        self,
        *,
        keys: Sequence[str],
        rows: Sequence[Sequence[Any]],
        rowcount: int,
        lastrowid: int | None,
    ) -> None:
        self.lastrowid = lastrowid
        self.rowcount = rowcount
        self._keys = tuple(keys)
        self._rows = list(rows)
        self._position = 0

    def _row(self, row: Any | None) -> DatabaseRow | None:
        if row is None:
            return None
        return DatabaseRow(self._keys, tuple(row))

    async def fetchone(self) -> DatabaseRow | None:
        if self._position >= len(self._rows):
            return None
        row = self._rows[self._position]
        self._position += 1
        return self._row(row)

    async def fetchall(self) -> list[DatabaseRow]:
        rows = self._rows[self._position :]
        self._position = len(self._rows)
        return [DatabaseRow(self._keys, tuple(row)) for row in rows]

    async def close(self) -> None:
        self._position = len(self._rows)

    def __aiter__(self) -> DatabaseCursor:
        return self

    async def __anext__(self) -> DatabaseRow:
        row = await self.fetchone()
        if row is None:
            raise StopAsyncIteration
        return row


class ExecuteOperation:
    """An execution that works with both ``await`` and ``async with``."""

    def __init__(
        self,
        connection: DatabaseConnection,
        statement: str,
        parameters: Sequence[Any] | Mapping[str, Any] | None,
    ) -> None:
        self._connection = connection
        self._statement = statement
        self._parameters = parameters
        self._cursor: DatabaseCursor | None = None

    async def _run(self) -> DatabaseCursor:
        if self._cursor is None:
            self._cursor = await self._connection._execute(
                self._statement,
                self._parameters,
            )
        return self._cursor

    def __await__(self):
        return self._run().__await__()

    async def __aenter__(self) -> DatabaseCursor:
        return await self._run()

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self._cursor is not None:
            await self._cursor.close()


class DatabaseConnection:
    """Dialect-aware async SQLAlchemy connection with the legacy cursor shape."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection
        self.dialect = connection.dialect.name
        self._transaction_lock = asyncio.Lock()
        self._transaction_owner: asyncio.Task[Any] | None = None

    def execute(
        self,
        statement: str,
        parameters: Sequence[Any] | Mapping[str, Any] | None = None,
    ) -> ExecuteOperation:
        return ExecuteOperation(self, statement, parameters)

    async def _execute(
        self,
        statement: str,
        parameters: Sequence[Any] | Mapping[str, Any] | None,
    ) -> DatabaseCursor:
        sql, bound = _driver_statement(self.dialect, statement, parameters)
        acquired = await self._acquire_transaction()
        try:
            result = await self._connection.exec_driver_sql(sql, bound)
            lastrowid = result.lastrowid if self.dialect == "sqlite" else None
            keys = tuple(result.keys()) if result.returns_rows else ()
            rows = result.fetchall() if result.returns_rows else ()
            cursor = DatabaseCursor(
                keys=keys,
                rows=rows,
                rowcount=result.rowcount,
                lastrowid=lastrowid,
            )
            result.close()
            if acquired and _is_read_only(statement):
                await self._connection.rollback()
                self._release_transaction()
            return cursor
        except BaseException:
            if self._transaction_owner is asyncio.current_task():
                await self._connection.rollback()
                self._release_transaction()
            raise

    async def commit(self) -> None:
        self._assert_transaction_owner()
        await self._connection.commit()
        self._release_transaction()

    async def rollback(self) -> None:
        self._assert_transaction_owner()
        await self._connection.rollback()
        self._release_transaction()

    async def close(self) -> None:
        if self._transaction_owner is not None:
            await self._connection.rollback()
            self._release_transaction()
        await self._connection.close()

    async def _acquire_transaction(self) -> bool:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("database operations require an asyncio task")
        if self._transaction_owner is task:
            return False
        await self._transaction_lock.acquire()
        self._transaction_owner = task
        return True

    def _assert_transaction_owner(self) -> None:
        task = asyncio.current_task()
        if self._transaction_owner is None:
            return
        if task is not self._transaction_owner:
            raise RuntimeError("database transaction belongs to another task")

    def _release_transaction(self) -> None:
        self._transaction_owner = None
        if self._transaction_lock.locked():
            self._transaction_lock.release()


def _is_read_only(statement: str) -> bool:
    normalized = statement.lstrip().upper()
    # PostgreSQL transaction advisory locks are invoked through SELECT but
    # must remain held until the caller commits the surrounding write unit.
    if "PG_ADVISORY_XACT_LOCK" in normalized:
        return False
    token = normalized.split(None, 1)[0] if normalized else ""
    return token in {"SELECT", "EXPLAIN", "PRAGMA"}


def _driver_statement(
    dialect: str,
    statement: str,
    parameters: Sequence[Any] | Mapping[str, Any] | None,
) -> tuple[str, Sequence[Any] | Mapping[str, Any]]:
    """Translate Yagami's positional SQL into the selected async driver style."""
    bound: Sequence[Any] | Mapping[str, Any]
    if isinstance(parameters, Mapping):
        bound = parameters
    else:
        bound = tuple(parameters or ())
    if dialect != "postgresql" or isinstance(bound, Mapping):
        return statement, bound
    return _qmark_to_asyncpg(statement), tuple(bound)


def _qmark_to_asyncpg(statement: str) -> str:
    """Convert unquoted qmark placeholders to asyncpg's numeric parameters."""
    output: list[str] = []
    quote: str | None = None
    index = 0
    parameter = 1
    while index < len(statement):
        char = statement[index]
        if quote is not None:
            output.append(char)
            if char == quote:
                if index + 1 < len(statement) and statement[index + 1] == quote:
                    output.append(statement[index + 1])
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"'}:
            quote = char
            output.append(char)
        elif char == "?":
            output.append(f"${parameter}")
            parameter += 1
        else:
            output.append(char)
        index += 1
    return "".join(output)


async def open_db(path: Path, *, database_url: str = "") -> DatabaseConnection:
    """Open the local SQLAlchemy data plane and apply compatible migrations."""
    global _db, _engine, _db_path, _database_url
    normalized_url = _normalize_database_url(database_url, path)
    if _db is not None and _database_url == normalized_url:
        return _db
    await close_db()

    if not database_url:
        path.parent.mkdir(parents=True, exist_ok=True)
        await _prepare_sqlite(path)
        engine = create_async_engine(normalized_url, poolclass=NullPool)
    else:
        engine = create_async_engine(normalized_url, pool_pre_ping=True)
    connection = await engine.connect()
    try:
        if connection.dialect.name == "sqlite":
            await _configure_sqlalchemy_sqlite(connection)
        else:
            await _verify_postgresql_schema(connection)
    except BaseException:
        await connection.close()
        await engine.dispose()
        raise

    _engine = engine
    _db = DatabaseConnection(connection)
    _db_path = path if not database_url else None
    _database_url = normalized_url
    return _db


def _normalize_database_url(database_url: str, path: Path) -> str:
    if not database_url:
        return f"sqlite+aiosqlite:///{path.resolve().as_posix()}"
    if database_url.startswith("sqlite+aiosqlite:///"):
        return database_url
    if database_url.startswith("sqlite:///"):
        return "sqlite+aiosqlite:///" + database_url.removeprefix("sqlite:///")
    if database_url.startswith("postgres://"):
        return "postgresql+asyncpg://" + database_url.removeprefix("postgres://")
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url.removeprefix("postgresql://")
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url
    raise ValueError("YAGAMI_DATABASE_URL must use SQLite or PostgreSQL with asyncpg")


async def close_db() -> None:
    global _db, _engine, _db_path, _database_url
    if _db is not None:
        await _db.close()
    if _engine is not None:
        await _engine.dispose()
    _db = None
    _engine = None
    _db_path = None
    _database_url = ""


def get_db() -> DatabaseConnection:
    if _db is None:
        raise RuntimeError("DB not opened; call open_db() first")
    return _db


@asynccontextmanager
async def exclusive_db() -> AsyncIterator[DatabaseConnection]:
    """Hold a dedicated serializable write transaction for lifecycle changes."""
    if _engine is None:
        raise RuntimeError("DB not opened; call open_db() first")
    async with _engine.connect() as connection:
        if connection.dialect.name == "sqlite":
            await _configure_sqlalchemy_sqlite(connection)
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            await connection.exec_driver_sql("BEGIN ISOLATION LEVEL SERIALIZABLE")
        wrapper = DatabaseConnection(connection)
        try:
            yield wrapper
        except BaseException:
            await connection.rollback()
            raise
        else:
            await connection.commit()


@asynccontextmanager
async def snapshot_db() -> AsyncIterator[DatabaseConnection]:
    """Open a consistent read transaction suitable for a streamed export."""
    if _engine is None:
        raise RuntimeError("DB not opened; call open_db() first")
    async with _engine.connect() as connection:
        if connection.dialect.name == "sqlite":
            await _configure_sqlalchemy_sqlite(connection)
            await connection.exec_driver_sql("BEGIN")
        else:
            await connection.exec_driver_sql("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            yield DatabaseConnection(connection)
        finally:
            await connection.rollback()


async def _configure_sqlalchemy_sqlite(connection: AsyncConnection) -> None:
    def configure(sync_connection) -> None:
        adapted = sync_connection.connection.dbapi_connection
        adapted.run_async(_configure_sqlite)

    await connection.run_sync(configure)


async def _prepare_sqlite(path: Path) -> None:
    connection = await aiosqlite.connect(str(path))
    try:
        connection.row_factory = aiosqlite.Row
        await _configure_sqlite(connection)
        await _run_sqlite_migrations(connection)
    finally:
        await connection.close()


async def _configure_sqlite(connection: aiosqlite.Connection) -> None:
    await _load_sqlite_vec(connection)
    await connection.execute("PRAGMA journal_mode=WAL")
    await connection.execute("PRAGMA foreign_keys=ON")
    await connection.execute("PRAGMA synchronous=NORMAL")
    await connection.commit()


async def _load_sqlite_vec(connection: aiosqlite.Connection) -> None:
    try:
        import sqlite_vec
    except ImportError:
        log.warning("sqlite-vec not installed; vector memory disabled")
        return
    try:
        await connection.enable_load_extension(True)
        await connection.load_extension(sqlite_vec.loadable_path())
        await connection.enable_load_extension(False)
    except Exception as exc:  # noqa: BLE001 - extension loading is platform-specific
        log.warning("failed to load sqlite-vec: %s; vector memory disabled", exc)


async def _run_sqlite_migrations(connection: aiosqlite.Connection) -> None:
    await connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)"
    )
    applied = {
        row[0] async for row in await connection.execute("SELECT version FROM schema_migrations")
    }
    for sql_path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        version = sql_path.stem
        if version in applied:
            continue
        log.info("applying migration %s", version)
        await connection.executescript(sql_path.read_text(encoding="utf-8"))
        await connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES(?, ?)",
            (version, now_ms()),
        )
        await connection.commit()


async def _verify_postgresql_schema(connection: AsyncConnection) -> None:
    result = await connection.exec_driver_sql("SELECT to_regclass('public.schema_migrations')")
    if result.scalar_one_or_none() is None:
        raise RuntimeError("PostgreSQL schema is not initialized; run `alembic upgrade head` first")
