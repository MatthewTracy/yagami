from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ..backends.base import BackendOptions, Message
from ..router.policy import RoutingPolicy
from ..telemetry.decisions import persist_decision
from .session import SessionStore

log = logging.getLogger("yagami.stream")


async def chat_endpoint(
    ws: WebSocket,
    sessions: SessionStore,
    policy: RoutingPolicy,
) -> None:
    await ws.accept()
    session_id: str | None = None
    gen_task: asyncio.Task | None = None
    receiver: asyncio.Task | None = None
    inbox: asyncio.Queue = asyncio.Queue()

    async def receive_loop():
        try:
            while True:
                msg = await ws.receive_json()
                await inbox.put(msg)
        except WebSocketDisconnect:
            await inbox.put(None)

    receiver = asyncio.create_task(receive_loop())

    try:
        if session_id is None:
            session_id = await sessions.new_session()
            await _send(ws, {"type": "session", "session_id": session_id})

        while True:
            payload = await inbox.get()
            if payload is None:
                break
            ptype = payload.get("type")
            if ptype == "cancel":
                if gen_task and not gen_task.done():
                    gen_task.cancel()
                continue
            if ptype == "load_session":
                sid = payload.get("session_id")
                if sid and await sessions.session_exists(sid):
                    session_id = sid
                    await _send(ws, {"type": "session", "session_id": session_id})
                continue

            user_text = payload.get("content", "")
            if not user_text or session_id is None:
                continue

            user_msg = Message(role="user", content=user_text)
            await sessions.append(session_id, user_msg)
            history = await sessions.history(session_id)

            decision = await policy.decide(history)
            decision_payload = {
                "backend": decision.backend.name,
                "is_local": decision.backend.is_local,
                "reason": decision.reason,
                "classification": decision.classification,
            }
            await persist_decision(
                session_id=session_id, user_text=user_text, decision=decision_payload
            )
            await _send(ws, {"type": "routing", **decision_payload})

            options = BackendOptions(lora_variant=decision.lora_variant)
            gen_task = asyncio.create_task(
                _stream_generation(ws, decision.backend, history, options)
            )
            try:
                assistant_text = await gen_task
            except asyncio.CancelledError:
                assistant_text = ""
                await _send(ws, {"type": "error", "content": "cancelled", "meta": {}})
                await _send(ws, {"type": "done", "content": "", "meta": {"cancelled": True}})
            finally:
                gen_task = None

            if assistant_text:
                await sessions.append(
                    session_id, Message(role="assistant", content=assistant_text)
                )
    except Exception:
        log.exception("stream error")
        try:
            await _send(ws, {"type": "error", "content": "internal stream error", "meta": {}})
        except Exception:
            pass
    finally:
        if receiver and not receiver.done():
            receiver.cancel()
        if gen_task and not gen_task.done():
            gen_task.cancel()


async def _stream_generation(ws, backend, history, options) -> str:
    pieces: list[str] = []
    async for chunk in backend.generate(history, options=options):
        if chunk["type"] == "text":
            pieces.append(chunk["content"])
        await _send(ws, chunk)
    return "".join(pieces)


async def _send(ws: WebSocket, msg: dict[str, Any]) -> None:
    await ws.send_text(json.dumps(msg))
