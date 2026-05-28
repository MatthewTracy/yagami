from __future__ import annotations

from collections import defaultdict
from uuid import uuid4

from ..backends.base import Message


class SessionStore:
    def __init__(self) -> None:
        self._messages: dict[str, list[Message]] = defaultdict(list)

    def new_session(self) -> str:
        sid = uuid4().hex
        self._messages[sid] = []
        return sid

    def append(self, session_id: str, message: Message) -> None:
        self._messages[session_id].append(message)

    def history(self, session_id: str) -> list[Message]:
        return list(self._messages[session_id])
