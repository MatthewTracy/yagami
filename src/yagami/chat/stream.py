from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ..backends.base import BackendOptions, ImageAttachment, Message
from ..backends.retry import generate_with_retry
from ..config import get_config
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


def set_memory_worker(worker) -> None:
    """Called from main.py's lifespan once the worker is up."""
    global _memory_worker
    _memory_worker = worker


def set_retriever(retriever: Retriever) -> None:
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
            images_raw = payload.get("images") or []
            if (not user_text and not images_raw) or session_id is None:
                continue

            attachments: list[ImageAttachment] = []
            for img in images_raw:
                try:
                    attachments.append(
                        ImageAttachment(media_type=img["media_type"], data_b64=img["data_b64"])
                    )
                except (KeyError, TypeError):
                    continue

            user_msg = Message(role="user", content=user_text, images=attachments or None)
            history_cache.append(user_msg)
            force_backend = payload.get("force_backend")
            # Images require a vision-capable backend; only Claude supports vision today.
            # Force_backend wins for explicit user choice.
            if attachments and not force_backend:
                force_backend = "anthropic"

            t_start = time.perf_counter()
            append_task = asyncio.create_task(sessions.append(session_id, user_msg))

            cfg = get_config()
            spend_blocked = False
            if cfg.routing.daily_spend_cap_usd > 0:
                today = await spend_today_usd()
                spend_blocked = today >= cfg.routing.daily_spend_cap_usd

            history_has_phi = _history_has_phi(history_cache)
            # `/reset` is a one-shot opt-out of the history-PHI gate. It
            # strips the prefix so the policy's fast-path / classifier sees
            # the clean prompt. Routing then runs normally - `/reset` alone
            # doesn't pick a backend.
            reset_override = parse_override(user_text)
            if reset_override.bypass_history_phi:
                history_has_phi = False
                cleaned = reset_override.stripped_text or ""
                history_cache[-1] = Message(
                    role="user", content=cleaned, images=attachments or None
                )
            decide_task = asyncio.create_task(
                policy.decide(
                    history_cache,
                    force_backend=force_backend,
                    spend_blocked=spend_blocked,
                    history_has_phi=history_has_phi,
                )
            )
            await append_task
            try:
                decision = await decide_task
            except OverrideRefused as exc:
                await _send(ws, {"type": "error", "content": str(exc), "meta": {"refused": True}})
                await _send(ws, {"type": "done", "content": "", "meta": {"refused": True}})
                continue
            t_classify_ms = int((time.perf_counter() - t_start) * 1000)

            # If the policy stripped a slash command, swap the last user message
            # so the backend sees the cleaned text. History cache stays as-is
            # (the user's original message is preserved in the chat).
            if decision.effective_user_text and decision.effective_user_text != user_text:
                history_cache[-1] = Message(role="user", content=decision.effective_user_text)

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
                lora_variant=decision.lora_variant,
                system_prompt=decision.system_prompt,
            )
            t_gen_start = time.perf_counter()
            first_token_ms_holder: list[int | None] = [None]
            image_count_holder = [0]

            # v0.2.16: build the per-turn message list for the backend. Recall
            # context is prepended HERE so it doesn't pollute the session
            # history_cache that future turns inherit.
            messages_for_backend: list[Message] = list(history_cache)
            if recall_hits:
                messages_for_backend = [recall_msg, *messages_for_backend]

            # v0.2.14: when the policy decided this turn needs tools AND the
            # chosen backend supports them, drive through tool_loop instead
            # of the plain generate path.
            from ..backends.anthropic import ClaudeBackend
            from ..backends.base import Capability
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
        if receiver and not receiver.done():
            receiver.cancel()
        if gen_task and not gen_task.done():
            gen_task.cancel()


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
