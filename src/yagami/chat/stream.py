from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ..backends.base import BackendOptions, Capability, ImageAttachment, Message
from ..backends.retry import generate_with_retry
from ..config import effective_routing, get_config
from ..memory import store as memory_store
from ..memory.retriever import Retriever
from ..router.fast_path import _has_phi, _has_secret
from ..router.overrides import parse as parse_override
from ..router.policy import OverrideRefused, RoutingPolicy
from ..router.schema import Sensitivity
from ..storage.db import get_db  # noqa: F401  (kept for future use)
from ..telemetry.costs import estimate_cost, rough_token_count, spend_today_usd
from ..telemetry.decisions import persist_decision, update_decision_timings
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


def _history_has_phi(history: list[Message]) -> bool:
    """True if any earlier message in the chat contains PHI or secret content.

    Skips the LAST user message - that's the CURRENT turn and is classified
    on its own merits. Only looks at prior turns.
    """
    if len(history) < 2:
        return False
    for m in history[:-1]:
        if _has_phi(m.content) or _has_secret(m.content):
            return True
    return False


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
    decide_task: asyncio.Task | None = None
    receiver: asyncio.Task | None = None
    inbox: asyncio.Queue = asyncio.Queue()
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
                if sid and await sessions.session_exists(sid):
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
            if not isinstance(images_raw, list):
                await _refuse_turn(ws, "images must be a list")
                continue
            if force_backend is not None and not isinstance(force_backend, str):
                await _refuse_turn(ws, "force_backend must be a string or null")
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
                force_backend = policy.first_vision_backend()
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

            t_start = time.perf_counter()
            append_task = asyncio.create_task(sessions.append(session_id, user_msg))

            cfg = get_config()
            # The gate reads the EFFECTIVE routing config - [routing] with the
            # active profile's overrides applied - so a profile's spend-cap
            # override or block_cloud flag actually bites.
            eff = effective_routing(cfg)
            spend_blocked = eff.block_cloud
            if not spend_blocked and eff.daily_spend_cap_usd > 0:
                today = await spend_today_usd()
                spend_blocked = today >= eff.daily_spend_cap_usd

            history_has_phi = _history_has_phi(history_cache)
            # `/reset` starts a fresh model context for this turn. It strips
            # the prefix so the policy sees the clean prompt, and the backend
            # receives only that prompt (never the PHI-tainted prior history).
            # The visible/persisted conversation remains intact.
            reset_override = parse_override(user_text)
            reset_context = reset_override.bypass_history_phi
            if reset_context:
                history_has_phi = False
                cleaned = reset_override.stripped_text or ""
                history_cache[-1] = Message(
                    role="user", content=cleaned, images=attachments or None
                )
            decision_history = _messages_for_backend(history_cache, reset_context=reset_context)
            decide_task = asyncio.create_task(
                policy.decide(
                    decision_history,
                    force_backend=force_backend,
                    spend_blocked=spend_blocked,
                    history_has_phi=history_has_phi,
                )
            )
            message_id = await append_task
            try:
                decision = await decide_task
            except asyncio.CancelledError:
                decide_task = None
                await _send(ws, {"type": "error", "content": "cancelled", "meta": {}})
                await _send(ws, {"type": "done", "content": "", "meta": {"cancelled": True}})
                continue
            except OverrideRefused as exc:
                decide_task = None
                await _refuse_turn(ws, str(exc))
                continue
            decide_task = None
            if connection_closed.is_set():
                break
            t_classify_ms = int((time.perf_counter() - t_start) * 1000)

            if attachments and Capability.VISION not in decision.backend.capabilities:
                # The message text is persisted, but the refused attachment
                # must not hitch a ride on a later turn's history.
                history_cache[-1] = Message(role="user", content=history_cache[-1].content)
                await sessions.delete_message_images(message_id)
                await _refuse_turn(
                    ws,
                    f"backend {decision.backend.name!r} cannot accept image attachments",
                )
                continue

            # If the policy stripped a slash command, swap the last user message
            # so the backend sees the cleaned text. The original message was
            # already persisted for display in the chat.
            if decision.effective_user_text and decision.effective_user_text != user_text:
                history_cache[-1] = Message(
                    role="user",
                    content=decision.effective_user_text,
                    images=attachments or None,
                )

            # Image-prompt auto-enhancement: if the chosen backend is an image
            # generator and the prompt is short, prepend a quality hint.
            if decision.backend.name == "stability":
                last = history_cache[-1]
                if len(last.content) < 40 and not last.content.startswith(_IMAGE_PROMPT_PREFIX):
                    history_cache[-1] = Message(
                        role="user", content=_IMAGE_PROMPT_PREFIX + last.content
                    )

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
                profile=cfg.routing.active_profile or None,
            )
            await _send(ws, {"type": "routing", "decision_id": decision_id, **decision_payload})

            # v0.2.16: cross-session recall. When the classifier flagged
            # needs_recall and a retriever is registered, fetch top-K
            # observations from prior sessions and inject them as a system
            # message ahead of the live turn. Always-PHI-safe: the
            # retriever drops PHI hits when the current turn isn't itself
            # PHI; and we skip retrieval entirely on cloud-text turns
            # whose history already contains PHI (policy refused anyway).
            try:
                recall_sens = Sensitivity(decision.classification.get("sensitivity", "none"))
            except (TypeError, ValueError):
                recall_sens = Sensitivity.NONE
            recall_hits = []
            if decision.classification.get("needs_recall") and _retriever is not None:
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

            options = BackendOptions(
                temperature=0.2 if decision.system_prompt else 0.7,
                lora_variant=decision.lora_variant,
                model_override=decision.model_override,
                system_prompt=decision.system_prompt,
            )
            t_gen_start = time.perf_counter()
            first_token_ms_holder: list[int | None] = [None]
            image_count_holder = [0]

            # v0.2.16: build the per-turn message list for the backend. Recall
            # context is prepended HERE so it doesn't pollute the session
            # history_cache that future turns inherit.
            messages_for_backend = _messages_for_backend(history_cache, reset_context=reset_context)
            if recall_hits:
                messages_for_backend = [recall_msg, *messages_for_backend]

            # v0.2.14: when the policy decided this turn needs tools AND the
            # chosen backend supports them, drive through tool_loop instead
            # of the plain generate path.
            from ..backends.anthropic import ClaudeBackend
            from ..router.schema import Sensitivity as _Sens

            use_tool_loop = (
                decision.use_tools
                and isinstance(decision.backend, ClaudeBackend)
                and Capability.TOOLS in decision.backend.capabilities
            )

            if use_tool_loop:
                try:
                    sens = _Sens(decision.classification.get("sensitivity", "none"))
                except (TypeError, ValueError):
                    sens = _Sens.NONE
                gen_task = asyncio.create_task(
                    _stream_tool_loop(
                        ws,
                        decision.backend,
                        messages_for_backend,
                        options,
                        t_gen_start,
                        first_token_ms_holder,
                        session_id=session_id,
                        sensitivity=sens,
                    )
                )
            else:
                gen_task = asyncio.create_task(
                    _stream_generation(
                        ws,
                        decision.backend,
                        messages_for_backend,
                        options,
                        t_gen_start,
                        first_token_ms_holder,
                        image_count_holder,
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
            tokens_in = sum(
                rough_token_count(m.content) for m in history_cache[:-1]
            ) + rough_token_count(history_cache[-1].content)
            tokens_out = rough_token_count(assistant_text)
            cost = estimate_cost(
                decision.backend,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                images=image_count_holder[0],
            )
            await update_decision_timings(
                decision_id,
                first_token_ms=first_token_ms_holder[0],
                total_ms=t_total_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
            )

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
                classification=decision.classification,
            )
    except Exception:
        log.exception("stream error")
        try:
            await _send(ws, {"type": "error", "content": "internal stream error", "meta": {}})
        except Exception:
            pass
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


async def _stream_generation(
    ws, backend, history, options, t_gen_start, first_token_holder, image_count_holder
) -> str:
    pieces: list[str] = []
    async for chunk in generate_with_retry(backend, history, options):
        if chunk["type"] in ("text", "image_url") and first_token_holder[0] is None:
            first_token_holder[0] = int((time.perf_counter() - t_gen_start) * 1000)
        if chunk["type"] == "text":
            pieces.append(chunk["content"])
        elif chunk["type"] == "image_url":
            image_count_holder[0] += 1
        await _send(ws, chunk)
    return "".join(pieces)


async def _stream_tool_loop(
    ws,
    backend,
    history,
    options,
    t_gen_start,
    first_token_holder,
    *,
    session_id,
    sensitivity,
) -> str:
    """v0.2.14: drive the multi-turn tool loop and forward each chunk to the WS.

    Returns the concatenated assistant text (without tool_call chunks) so
    spend bookkeeping in the caller can rough-count tokens normally.
    """
    from ..router import tool_loop

    pieces: list[str] = []
    async for chunk in tool_loop.run(
        backend,
        history,
        options,
        session_id=session_id,
        session_sensitivity=sensitivity,
    ):
        if chunk["type"] in ("text", "tool_call") and first_token_holder[0] is None:
            first_token_holder[0] = int((time.perf_counter() - t_gen_start) * 1000)
        if chunk["type"] == "text":
            pieces.append(chunk["content"])
        await _send(ws, chunk)
    return "".join(pieces)


async def _send(ws: WebSocket, msg: dict[str, Any]) -> None:
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
