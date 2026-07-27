from __future__ import annotations

import asyncio

import pytest

from yagami.storage.db import get_db


@pytest.mark.asyncio
async def test_shared_connection_serializes_task_transactions(fresh_db) -> None:
    async def insert(index: int) -> None:
        db = get_db()
        await db.execute(
            "INSERT INTO sessions(id, created_at, updated_at) VALUES(?, ?, ?)",
            (f"concurrent-{index}", index, index),
        )
        await asyncio.sleep(0.001)
        await db.commit()

    await asyncio.gather(*(insert(index) for index in range(20)))
    async with fresh_db.execute(
        "SELECT COUNT(*) FROM sessions WHERE id LIKE 'concurrent-%'"
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 20


@pytest.mark.asyncio
async def test_statement_failure_rolls_back_and_releases_transaction(fresh_db) -> None:
    await fresh_db.execute(
        "INSERT INTO sessions(id, created_at, updated_at) VALUES('rolled-back', 0, 0)"
    )
    with pytest.raises(Exception):
        await fresh_db.execute("INSERT INTO table_that_does_not_exist(value) VALUES(1)")

    await fresh_db.execute(
        "INSERT INTO sessions(id, created_at, updated_at) VALUES('after-error', 0, 0)"
    )
    await fresh_db.commit()
    async with fresh_db.execute(
        "SELECT id FROM sessions WHERE id IN ('rolled-back','after-error') ORDER BY id"
    ) as cursor:
        rows = await cursor.fetchall()
    assert [row[0] for row in rows] == ["after-error"]
