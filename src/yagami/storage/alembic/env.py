from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from yagami.storage.db import _configure_sqlalchemy_sqlite, _normalize_database_url
from yagami.storage.schema import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.getenv("YAGAMI_DATABASE_URL") or os.getenv("YAGAMI_ALEMBIC_DATABASE_URL", "")
if database_url and not config.attributes.get("programmatic_url"):
    normalized_url = (
        database_url
        if database_url.startswith("sqlite+aiosqlite:")
        else _normalize_database_url(
            database_url,
            __import__("pathlib").Path("yagami.db"),
        )
    )
    config.set_main_option(
        "sqlalchemy.url",
        normalized_url,
    )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=metadata,
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        if connection.dialect.name == "sqlite":
            await _configure_sqlalchemy_sqlite(connection)
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
