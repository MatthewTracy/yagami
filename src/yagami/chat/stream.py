from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ..backends.base import BackendOptions, Message
from ..router.policy import RoutingPolicy
from .session import SessionStore

log = logging.getLogger("yagami.stream")


async def chat_endpoint(ws: WebSocket, sessions: SessionStore, policy: RoutingPolicy) -> None:
    await ws.accept()
    session_id = sessions.new_session()
    await _send(ws, {"type": "session", "session_id": session_id})
    try:
        while True:
            payload = await ws.receive_json()
            user_text = payload.get("content", "")
            if not user_text:
                continue
            user_msg = Message(role="user", content=user_text)
            sessions.append(session_id, user_msg)
            history = sessions.history(session_id)

            decision = await policy.decide(history)
            await _send(
                ws,
                {
                    "type": "routing",
                    "backend": decision.backend.name,
                    "is_local": decision.backend.is_local,
                    "reason": decision.reason,
                    "classification": decision.classification,
                },
            )

            options = BackendOptions(lora_variant=decision.lora_variant)
            assistant_chunks: list[str] = []
            async for chunk in decision.backend.generate(history, options=options):
                if chunk["type"] == "text":
                    assistant_chunks.append(chunk["content"])
                await _send(ws, chunk)

            if assistant_chunks:
                sessions.append(
                    session_id,
                    Message(role="assistant", content="".join(assistant_chunks)),
                )
    except WebSocketDisconnect:
        log.info("client disconnected from session %s", session_id)
    except Exception as exc:
        log.exception("stream error")
        await _send(ws, {"type": "error", "content": str(exc), "meta": {}})


async def _send(ws: WebSocket, msg: dict[str, Any]) -> None:
    await ws.send_text(json.dumps(msg))
