from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ..backends.base import BackendOptions, Message
from ..router.policy import RoutingPolicy
from ..telemetry.decisions import persist_decision, update_decision_timings
from .session import SessionStore

log = logging.getLogger("yagami.stream")


async def chat_endpoint(
    ws: WebSocket,
    sessions: SessionStore,
    policy: RoutingPolicy,
) -> None:
    await ws.accept()
    session_id: str | None = None
    history_cache: list[Message] = []
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
            history_cache = []
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
                    history_cache = await sessions.history(session_id)
                    await _send(ws, {"type": "session", "session_id": session_id})
                continue

            user_text = payload.get("content", "")
            if not user_text or session_id is None:
                continue

            user_msg = Message(role="user", content=user_text)
            history_cache.append(user_msg)

            t_start = time.perf_counter()
            append_task = asyncio.create_task(sessions.append(session_id, user_msg))
            decide_task = asyncio.create_task(policy.decide(history_cache))
            await append_task
            decision = await decide_task
            t_classify_ms = int((time.perf_counter() - t_start) * 1000)

            decision_payload = {
                "backend": decision.backend.name,
                "is_local": decision.backend.is_local,
                "reason": decision.reason,
                "classification": decision.classification,
            }
            decision_id = await persist_decision(
                session_id=session_id,
                user_text=user_text,
                decision=decision_payload,
                timings={"classify_ms": t_classify_ms},
            )
            await _send(ws, {"type": "routing", **decision_payload})

            options = BackendOptions(
                lora_variant=decision.lora_variant,
                system_prompt=decision.system_prompt,
            )
            t_gen_start = time.perf_counter()
            first_token_ms_holder: list[int | None] = [None]
            gen_task = asyncio.create_task(
                _stream_generation(
                    ws, decision.backend, history_cache, options, t_gen_start, first_token_ms_holder
                )
            )
            try:
                assistant_text = await gen_task
            except asyncio.CancelledError:
                assistant_text = ""
                await _send(ws, {"type": "error", "content": "cancelled", "meta": {}})
                await _send(ws, {"type": "done", "content": "", "meta": {"cancelled": True}})
            finally:
                gen_task = None

            t_total_ms = int((time.perf_counter() - t_start) * 1000)
            await update_decision_timings(
                decision_id,
                first_token_ms=first_token_ms_holder[0],
                total_ms=t_total_ms,
            )

            if assistant_text:
                assistant_msg = Message(role="assistant", content=assistant_text)
                history_cache.append(assistant_msg)
                await sessions.append(session_id, assistant_msg)
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


async def _stream_generation(ws, backend, history, options, t_gen_start, first_token_holder) -> str:
    pieces: list[str] = []
    async for chunk in backend.generate(history, options=options):
        if chunk["type"] in ("text", "image_url") and first_token_holder[0] is None:
            first_token_holder[0] = int((time.perf_counter() - t_gen_start) * 1000)
        if chunk["type"] == "text":
            pieces.append(chunk["content"])
        await _send(ws, chunk)
    return "".join(pieces)


async def _send(ws: WebSocket, msg: dict[str, Any]) -> None:
    await ws.send_text(json.dumps(msg))
