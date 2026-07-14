from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ..backends.base import Capability, ImageAttachment, Message
from ..config import get_config
from ..gateway.service import GatewayError, GatewayRequestOptions, GatewayService
from ..memory import store as memory_store
from ..memory.retriever import Retriever
from ..policy import PolicyContext, PolicyMode
from ..router.overrides import parse as parse_override
from ..router.schema import Sensitivity
from .session import SessionStore

# Module-level worker + retriever handles so main.py can register them
# without threading through every invocation.
_memory_worker: "object | None" = None
_retriever: Retriever | None = None


def set_memory_worker(worker: object | None) -> None:
    """Called from main.py's lifespan once the worker is up."""
    global _memory_worker
    _memory_worker = worker


def set_retriever(retriever: Retriever | None) -> None:
    """Called from main.py's lifespan once the retriever is constructed."""
    global _retriever
    _retriever = retriever


_IMAGE_PROMPT_PREFIX = "high quality, detailed, photorealistic: "
_MAX_CHAT_TEXT_CHARS = 1_000_000
_MAX_CHAT_IMAGES = 4
_MAX_CHAT_IMAGE_B64_CHARS = 4 * 27_962_028
_MAX_INBOX_MESSAGES = 32


log = logging.getLogger("yagami.stream")


async def chat_endpoint(
    ws: WebSocket,
    sessions: SessionStore,
    gateway: GatewayService,
) -> None:
    await ws.accept()
    session_id: str | None = None
    history_cache: list[Message] = []
    gen_task: asyncio.Task | None = None
    decide_task: asyncio.Task | None = None
    receiver: asyncio.Task | None = None
    inbox: asyncio.Queue = asyncio.Queue(maxsize=_MAX_INBOX_MESSAGES)
    connection_closed = asyncio.Event()

    async def receive_loop():
        try:
            while True:
                msg = await ws.receive_json()
                if isinstance(msg, dict) and msg.get("type") == "cancel":
                    # Active work is awaited by the main loop, so queueing a
                    # cancel behind it can never interrupt it. Cancel the live
                    # task directly from this receiver task instead.
                    for task in (decide_task, gen_task):
                        if task is not None and not task.done():
                            task.cancel()
                    continue
                await inbox.put(msg)
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001 - malformed frames must not strand the handler
            log.exception("websocket receive failed")
        finally:
            connection_closed.set()
            for task in (decide_task, gen_task):
                if task is not None and not task.done():
                    task.cancel()
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
            if not isinstance(payload, dict):
                await _refuse_turn(ws, "message payload must be a JSON object")
                continue
            ptype = payload.get("type")
            if ptype == "cancel":
                if gen_task and not gen_task.done():
                    gen_task.cancel()
                continue
            if ptype == "load_session":
                sid = payload.get("session_id")
                if isinstance(sid, str) and len(sid) <= 128 and await sessions.session_exists(sid):
                    session_id = sid
                    history_cache = await sessions.history(session_id)
                    await _send(ws, {"type": "session", "session_id": session_id})
                else:
                    await _send(
                        ws,
                        {"type": "error", "content": "session not found", "meta": {}},
                    )
                continue
            if ptype is not None:
                await _refuse_turn(ws, f"unknown message type {ptype!r}")
                continue

            user_text = payload.get("content", "")
            images_raw = payload.get("images") or []
            force_backend = payload.get("force_backend")
            if not isinstance(user_text, str):
                await _refuse_turn(ws, "content must be a string")
                continue
            if len(user_text) > _MAX_CHAT_TEXT_CHARS:
                await _refuse_turn(ws, "content exceeds 1,000,000 characters")
                continue
            if not isinstance(images_raw, list):
                await _refuse_turn(ws, "images must be a list")
                continue
            if len(images_raw) > _MAX_CHAT_IMAGES:
                await _refuse_turn(ws, "at most 4 images are supported per message")
                continue
            if force_backend is not None and not isinstance(force_backend, str):
                await _refuse_turn(ws, "force_backend must be a string or null")
                continue
            if isinstance(force_backend, str) and len(force_backend) > 128:
                await _refuse_turn(ws, "force_backend exceeds 128 characters")
                continue
            if (
                sum(
                    len(image.get("data_b64", ""))
                    for image in images_raw
                    if isinstance(image, dict)
                )
                > _MAX_CHAT_IMAGE_B64_CHARS
            ):
                await _refuse_turn(ws, "combined image payload is too large")
                continue
            if session_id is None:
                continue

            attachments: list[ImageAttachment] = []
            invalid_attachment = False
            for img in images_raw:
                try:
                    attachments.append(
                        ImageAttachment(media_type=img["media_type"], data_b64=img["data_b64"])
                    )
                except (KeyError, TypeError, ValueError):
                    invalid_attachment = True
                    break
            if invalid_attachment:
                await _refuse_turn(ws, "invalid image attachment")
                continue
            if not user_text and not attachments:
                await _refuse_turn(ws, "message must include text or a valid image")
                continue

            user_msg = Message(role="user", content=user_text, images=attachments or None)
            history_cache.append(user_msg)
            # Images require a vision-capable backend. Force_backend wins for
            # explicit user choice; otherwise pick the first configured
            # vision backend (anthropic preferred, then gemini/openai/
            # openrouter - see RoutingPolicy.first_vision_backend).
            if attachments and not force_backend:
                force_backend = gateway.routing_policy.first_vision_backend()
                if force_backend is None:
                    history_cache.pop()  # not persisted; keep cache/DB consistent
                    await _send(
                        ws,
                        {
                            "type": "error",
                            "content": (
                                "no vision-capable backend configured - set an API key for "
                                "Anthropic, Gemini, OpenAI, or OpenRouter to send images"
                            ),
                            "meta": {"refused": True},
                        },
                    )
                    await _send(ws, {"type": "done", "content": "", "meta": {"refused": True}})
                    continue

            append_task = asyncio.create_task(sessions.append(session_id, user_msg))
            cfg = get_config()
            # `/reset` starts a fresh model context for this turn. It strips
            # the prefix so the policy sees the clean prompt, and the backend
            # receives only that prompt (never the PHI-tainted prior history).
            # The visible/persisted conversation remains intact.
            reset_override = parse_override(user_text)
            reset_context = reset_override.bypass_history_phi
            if reset_context:
                cleaned = reset_override.stripped_text or ""
                history_cache[-1] = Message(
                    role="user", content=cleaned, images=attachments or None
                )
            message_id = await append_task

            context = PolicyContext(
                project_id="local",
                purpose="interactive-chat",
                session_id=session_id,
            )
            request_options = GatewayRequestOptions()
            model = force_backend or "yagami-auto"
            decision_history = _messages_for_backend(history_cache, reset_context=reset_context)
            decide_task = asyncio.create_task(
                gateway.prepare(
                    messages=decision_history,
                    model=model,
                    context=context,
                    options=request_options,
                    persist=False,
                    raise_on_deny=False,
                )
            )
            try:
                prepared = await decide_task
            except asyncio.CancelledError:
                decide_task = None
                await _send(ws, {"type": "error", "content": "cancelled", "meta": {}})
                await _send(ws, {"type": "done", "content": "", "meta": {"cancelled": True}})
                continue
            except GatewayError as exc:
                decide_task = None
                await _refuse_turn(ws, exc.message)
                continue
            decide_task = None
            if connection_closed.is_set():
                break

            if attachments and Capability.VISION not in prepared.decision.backend.capabilities:
                # The message text is persisted, but the refused attachment
                # must not hitch a ride on a later turn's history.
                history_cache[-1] = Message(role="user", content=history_cache[-1].content)
                await sessions.delete_message_images(message_id)
                await _refuse_turn(
                    ws,
                    f"backend {prepared.decision.backend.name!r} cannot accept image attachments",
                )
                continue

            # v0.2.16: cross-session recall. When the classifier flagged
            # needs_recall and a retriever is registered, fetch top-K
            # observations from prior sessions and inject them as a system
            # message ahead of the live turn. Always-PHI-safe: the
            # retriever drops PHI hits when the current turn isn't itself
            # PHI; and we skip retrieval entirely on cloud-text turns
            # whose history already contains PHI (policy refused anyway).
            try:
                recall_sens = Sensitivity(
                    prepared.decision.classification.get("sensitivity", "none")
                )
            except (TypeError, ValueError):
                recall_sens = Sensitivity.NONE
            recall_hits = []
            if prepared.decision.classification.get("needs_recall") and _retriever is not None:
                try:
                    recall_hits = await _retriever.fetch(
                        history_cache[-1].content,
                        k=5,
                        exclude_session=session_id,
                        current_sens=recall_sens,
                    )
                except Exception:  # noqa: BLE001
                    log.exception("retriever failed; continuing without recall")
                if recall_hits:
                    snippet = "\n".join(
                        f"- ({h.role}, {h.session_id[:8]}): {h.text[:400]}" for h in recall_hits
                    )
                    recall_msg = Message(
                        role="system",
                        content=(
                            "Cross-session memory - these are excerpts from "
                            "earlier conversations the user has had with you. "
                            "Use them only if directly relevant to the current turn.\n\n" + snippet
                        ),
                    )
                    await _send(
                        ws,
                        {
                            "type": "recall",
                            "content": "",
                            "meta": {
                                "hits": [
                                    {
                                        "id": h.id,
                                        "role": h.role,
                                        "text": h.text[:200],
                                        "session_id": h.session_id,
                                        "source": h.source,
                                        "distance": h.distance,
                                    }
                                    for h in recall_hits
                                ]
                            },
                        },
                    )
            if recall_hits:
                prepared = await gateway.prepare(
                    messages=[recall_msg, *decision_history],
                    model=model,
                    context=context,
                    options=request_options,
                    persist=False,
                    raise_on_deny=False,
                )
            prepared.audit_user_text = user_text
            await gateway.persist_prepared(
                prepared,
                storage_session_id=session_id,
                channel="chat",
                profile=cfg.routing.active_profile or None,
            )
            decision_payload = {
                "backend": prepared.decision.backend.name,
                "is_local": prepared.decision.backend.is_local,
                "reason": prepared.decision.reason,
                "classification": prepared.decision.classification,
                "policy": prepared.policy.passport(),
            }
            await _send(
                ws,
                {"type": "routing", "decision_id": prepared.decision_id, **decision_payload},
            )

            if prepared.policy.denied and prepared.policy.mode == PolicyMode.ENFORCE:
                await _refuse_turn(ws, "request denied by Yagami policy")
                continue

            if prepared.decision.backend.name == "stability":
                for index in range(len(prepared.messages) - 1, -1, -1):
                    last = prepared.messages[index]
                    if last.role == "user":
                        if len(last.content) < 40 and not last.content.startswith(
                            _IMAGE_PROMPT_PREFIX
                        ):
                            prepared.messages[index] = last.model_copy(
                                update={"content": _IMAGE_PROMPT_PREFIX + last.content}
                            )
                        break

            gen_task = asyncio.create_task(_stream_gateway(ws, gateway, prepared))
            try:
                assistant_text = await gen_task
            except asyncio.CancelledError:
                assistant_text = ""
                await _send(ws, {"type": "error", "content": "cancelled", "meta": {}})
                await _send(ws, {"type": "done", "content": "", "meta": {"cancelled": True}})
            finally:
                gen_task = None

            if assistant_text:
                assistant_msg = Message(role="assistant", content=assistant_text)
                history_cache.append(assistant_msg)
                await sessions.append(session_id, assistant_msg)

            # v0.2.15 write gate: queue this turn into cross-session memory.
            # SECRET sessions never write; SIMPLE_QA + short turns skipped.
            # Async - never blocks the WS, errors are logged not raised.
            await _maybe_queue_memory(
                session_id=session_id,
                user_text=user_text,
                assistant_text=assistant_text,
                classification=prepared.decision.classification,
            )
    except Exception:
        log.exception("stream error")
        try:
            await _send(ws, {"type": "error", "content": "internal stream error", "meta": {}})
        except Exception:
            log.debug("failed to report stream error to disconnected client", exc_info=True)
    finally:
        pending_tasks: list[asyncio.Task] = []
        if receiver and not receiver.done():
            receiver.cancel()
            pending_tasks.append(receiver)
        if gen_task and not gen_task.done():
            gen_task.cancel()
            pending_tasks.append(gen_task)
        if decide_task and not decide_task.done():
            decide_task.cancel()
            pending_tasks.append(decide_task)
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)


async def _stream_gateway(ws: WebSocket, gateway: GatewayService, prepared) -> str:
    pieces: list[str] = []
    async for chunk in gateway.stream(prepared):
        if chunk["type"] == "text":
            pieces.append(chunk["content"])
        await _send(ws, chunk)
    return "".join(pieces)


async def _send(ws: WebSocket, msg: Mapping[str, Any]) -> None:
    await ws.send_text(json.dumps(msg))


async def _refuse_turn(ws: WebSocket, content: str) -> None:
    await _send(ws, {"type": "error", "content": content, "meta": {"refused": True}})
    await _send(ws, {"type": "done", "content": "", "meta": {"refused": True}})


def _messages_for_backend(history: list[Message], *, reset_context: bool = False) -> list[Message]:
    """Return the model-visible history for the current turn.

    `/reset` is deliberately implemented by removing earlier messages from
    the backend request, not merely by disabling the policy's history-PHI
    flag. Otherwise a cloud route could still receive the sensitive history
    the gate was meant to protect.
    """
    if reset_context and history:
        return [history[-1]]
    return list(history)


async def _maybe_queue_memory(
    *,
    session_id: str,
    user_text: str,
    assistant_text: str,
    classification: dict,
) -> None:
    """Write gate for cross-session memory.

    Rules:
    - secret sensitivity: nothing written (defense in depth - store also
      drops these, but skipping here saves the call).
    - simple_qa + short user message: skip (greetings, thanks, "lol").
    - everything else: queue both the user turn AND the assistant response,
      tagged with the turn's classified sensitivity.
    """
    try:
        sens = Sensitivity(classification.get("sensitivity", "none"))
    except (TypeError, ValueError):
        sens = Sensitivity.NONE
    if sens == Sensitivity.SECRET:
        return

    intent = classification.get("intent", "simple_qa")
    skip_user = intent == "simple_qa" and len(user_text.strip()) < memory_store.MIN_REMEMBER_CHARS

    try:
        if not skip_user and user_text:
            await memory_store.queue_observation(
                session_id=session_id,
                role="user",
                text=user_text,
                sensitivity=sens,
            )
        if assistant_text:
            await memory_store.queue_observation(
                session_id=session_id,
                role="assistant",
                text=assistant_text,
                sensitivity=sens,
            )
    except Exception:  # noqa: BLE001 - memory failures NEVER break the chat
        log.exception("memory write failed; chat continues")
        return
    if _memory_worker is not None and hasattr(_memory_worker, "nudge"):
        _memory_worker.nudge()
