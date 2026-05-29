from __future__ import annotations

from uuid import uuid4

from ..backends.base import Message
from ..storage.db import get_db, now_ms


class SessionStore:
    async def new_session(self) -> str:
        sid = uuid4().hex
        ts = now_ms()
        db = get_db()
        await db.execute(
            "INSERT INTO sessions(id, created_at, updated_at, title) VALUES(?, ?, ?, NULL)",
            (sid, ts, ts),
        )
        await db.commit()
        return sid

    async def session_exists(self, session_id: str) -> bool:
        db = get_db()
        async with db.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)) as cur:
            return (await cur.fetchone()) is not None

    async def append(self, session_id: str, message: Message) -> None:
        ts = now_ms()
        db = get_db()
        await db.execute(
            "INSERT INTO messages(session_id, role, content, created_at) VALUES(?, ?, ?, ?)",
            (session_id, message.role, message.content, ts),
        )
        await db.execute(
            "UPDATE sessions SET updated_at=?, title=COALESCE(title, ?) WHERE id=?",
            (ts, message.content[:80] if message.role == "user" else None, session_id),
        )
        await db.commit()

    async def history(self, session_id: str) -> list[Message]:
        db = get_db()
        async with db.execute(
            "SELECT role, content FROM messages WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        ) as cur:
            return [Message(role=row["role"], content=row["content"]) async for row in cur]

    async def list_sessions(self, limit: int = 50) -> list[dict]:
        db = get_db()
        async with db.execute(
            "SELECT id, created_at, updated_at, title FROM sessions"
            " ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            return [dict(row) async for row in cur]
