from __future__ import annotations

import base64
from uuid import uuid4

from ..backends.base import Message
from ..storage.db import get_db, now_ms


class SessionStore:
    async def new_session(
        self,
        *,
        session_id: str | None = None,
        title: str | None = None,
        channel: str = "chat",
        project_id: str | None = None,
    ) -> str:
        sid = session_id or uuid4().hex
        ts = now_ms()
        db = get_db()
        await db.execute(
            "INSERT INTO sessions(id, created_at, updated_at, title, channel, project_id)"
            " VALUES(?, ?, ?, ?, ?, ?)",
            (sid, ts, ts, title, channel, project_id),
        )
        await db.commit()
        return sid

    async def ensure_gateway_session(self, session_id: str, *, project_id: str) -> str:
        """Create the hidden audit parent for stateless gateway decisions."""
        db = get_db()
        ts = now_ms()
        await db.execute(
            "INSERT INTO sessions(id, created_at, updated_at, title, channel, project_id)"
            " VALUES(?, ?, ?, ?, 'gateway', ?)"
            " ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at",
            (session_id, ts, ts, f"Gateway: {project_id}"[:120], project_id),
        )
        await db.commit()
        return session_id

    async def session_exists(self, session_id: str) -> bool:
        db = get_db()
        async with db.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)) as cur:
            return (await cur.fetchone()) is not None

    async def append(self, session_id: str, message: Message) -> int:
        ts = now_ms()
        title = None
        if message.role == "user":
            title = message.content.strip()[:80] or ("Image attachment" if message.images else None)
        db = get_db()
        cur = await db.execute(
            "INSERT INTO messages(session_id, role, content, created_at) VALUES(?, ?, ?, ?)",
            (session_id, message.role, message.content, ts),
        )
        message_id = cur.lastrowid
        if message_id is None:
            raise RuntimeError("message insert did not return a row id")
        for image in message.images or []:
            await db.execute(
                "INSERT INTO message_attachments(message_id, media_type, data, created_at)"
                " VALUES(?, ?, ?, ?)",
                (message_id, image.media_type, base64.b64decode(image.data_b64), ts),
            )
        await db.execute(
            "UPDATE sessions SET updated_at=?, title=COALESCE(title, ?) WHERE id=?",
            (ts, title, session_id),
        )
        await db.commit()
        return int(message_id)

    async def history(self, session_id: str) -> list[Message]:
        db = get_db()
        async with db.execute(
            """SELECT m.id, m.role, m.content,
                      a.id AS attachment_id, a.media_type, a.data
                 FROM messages m
                 LEFT JOIN message_attachments a ON a.message_id = m.id
                WHERE m.session_id=?
                ORDER BY m.id ASC, a.id ASC""",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        messages: list[Message] = []
        current_id: int | None = None
        current: dict | None = None
        for row in rows:
            message_id = int(row["id"])
            if message_id != current_id:
                if current is not None:
                    if not current["images"]:
                        current["images"] = None
                    messages.append(Message.model_validate(current))
                current_id = message_id
                current = {"role": row["role"], "content": row["content"], "images": []}
            if row["attachment_id"] is not None and current is not None:
                current["images"].append(
                    {
                        "media_type": row["media_type"],
                        "data_b64": base64.b64encode(row["data"]).decode("ascii"),
                    }
                )
        if current is not None:
            if not current["images"]:
                current["images"] = None
            messages.append(Message.model_validate(current))
        return messages

    async def delete_message_images(self, message_id: int) -> None:
        """Remove persisted images from a turn refused by a text-only backend."""
        db = get_db()
        await db.execute("DELETE FROM message_attachments WHERE message_id=?", (message_id,))
        await db.commit()

    async def list_sessions(self, limit: int = 50) -> list[dict]:
        db = get_db()
        async with db.execute(
            "SELECT id, created_at, updated_at, title FROM sessions"
            " WHERE channel='chat' ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            return [dict(row) async for row in cur]

    async def rename(self, session_id: str, title: str) -> bool:
        db = get_db()
        ts = now_ms()
        cur = await db.execute(
            "UPDATE sessions SET title=?, updated_at=? WHERE id=?",
            (title.strip()[:120] or None, ts, session_id),
        )
        await db.commit()
        return cur.rowcount > 0

    async def delete(self, session_id: str) -> bool:
        db = get_db()
        # FK ON DELETE CASCADE on messages and decisions takes care of children.
        cur = await db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        await db.commit()
        return cur.rowcount > 0
