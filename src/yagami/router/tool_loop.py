"""Multi-turn tool-use driver. Anthropic-only for v0.2.14.

The shape: each loop iteration calls the Anthropic Messages API with the
running conversation. If the response is plain text, we stream it and stop.
If the response includes tool_use content blocks, we run each requested
skill, append the assistant's tool_use turn + a user turn carrying the
tool_results, then iterate. Hard cap at MAX_TURNS so a confused model
can't infinite-loop.

Skill execution is concurrent within a single turn - multiple tool_use
blocks in one response run in parallel via asyncio.gather. Each tool's
result yields its own `tool_call` chunk on the WebSocket so the UI can
render an inline card before the next text turn arrives.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from anthropic import APIError

from ..backends.anthropic import ClaudeBackend
from ..backends.base import BackendChunk, BackendOptions, Message
from ..skills.adapters import to_anthropic_tools
from ..skills.base import Skill, SkillContext, SkillResult
from ..skills.registry import discover_skills
from ..router.schema import Sensitivity

log = logging.getLogger("yagami.tool_loop")

MAX_TURNS = 8  # hard ceiling so a model can't infinite-loop on its own tools


async def _run_skill(skill: Skill, args: dict, ctx: SkillContext) -> SkillResult:
    """Wrap skill invocation: never raise, enforce sensitivity ceiling."""
    if _sensitivity_rank(ctx.session_sensitivity) > _sensitivity_rank(skill.sensitivity_ceiling):
        return SkillResult(
            ok=False,
            error=(
                f"skill {skill.name} refused: session sensitivity "
                f"{ctx.session_sensitivity.value} exceeds ceiling "
                f"{skill.sensitivity_ceiling.value}"
            ),
        )
    try:
        return await skill.run(args, ctx)
    except Exception as exc:  # noqa: BLE001 - skills must never raise; defense in depth
        log.warning("skill %s raised %s; treating as error", skill.name, exc)
        return SkillResult(ok=False, error=f"unexpected: {exc}")


def _sensitivity_rank(s: Sensitivity) -> int:
    order = {
        Sensitivity.NONE: 0,
        Sensitivity.PHI: 1,
        Sensitivity.PHI_MEDICAL: 2,
        Sensitivity.SECRET: 3,
    }
    return order.get(s, 0)


async def run(
    backend,
    messages: list[Message],
    options: BackendOptions,
    *,
    session_id: str,
    session_sensitivity: Sensitivity = Sensitivity.NONE,
    skills: dict[str, Skill] | None = None,
) -> AsyncIterator[BackendChunk]:
    """Drive the conversation through tool-use cycles until the model emits
    a turn with no tool calls. Dispatches on wire format: Anthropic gets the
    Messages-API loop, OpenAI-compatible backends (openai, mistral, groq,
    openrouter, gemini) get the chat-completions loop. Anything else
    degrades to plain generate - same principle as having no tools at all.
    """
    from ..backends.openai import OpenAIBackend
    from ..backends.openai_compat import OpenAICompatBackend

    skills_map = skills if skills is not None else discover_skills()
    if not skills_map:
        async for chunk in backend.generate(messages, options=options):
            yield chunk
        return

    if isinstance(backend, ClaudeBackend):
        loop = _run_anthropic
    elif isinstance(backend, (OpenAIBackend, OpenAICompatBackend)):
        loop = _run_openai
    else:
        async for chunk in backend.generate(messages, options=options):
            yield chunk
        return

    async for chunk in loop(
        backend,
        messages,
        options,
        session_id=session_id,
        session_sensitivity=session_sensitivity,
        skills_map=skills_map,
    ):
        yield chunk


async def _run_anthropic(
    backend: ClaudeBackend,
    messages: list[Message],
    options: BackendOptions,
    *,
    session_id: str,
    session_sensitivity: Sensitivity,
    skills_map: dict[str, Skill],
) -> AsyncIterator[BackendChunk]:
    tools = to_anthropic_tools(list(skills_map.values()))

    system_parts = [m.content for m in messages if m.role == "system"]
    if options.system_prompt:
        system_parts = [options.system_prompt]

    chat: list[dict] = []
    for m in messages:
        if m.role not in ("user", "assistant"):
            continue
        if m.images:
            blocks: list[dict] = []
            for img in m.images:
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": img.media_type,
                            "data": img.data_b64,
                        },
                    }
                )
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            chat.append({"role": m.role, "content": blocks})
        else:
            chat.append({"role": m.role, "content": m.content})

    ctx = SkillContext(session_id=session_id, session_sensitivity=session_sensitivity)

    for turn in range(MAX_TURNS):
        kwargs: dict = {
            "model": backend._config.model,
            "max_tokens": options.max_tokens or backend._config.max_tokens,
            "temperature": options.temperature,
            "messages": chat,
            "tools": tools,
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)

        # We don't stream tool-use turns because we need the complete tool_use
        # block before we can run the skill. Streaming the FINAL text turn
        # would be nicer; punt on that for now - non-streaming Messages.create
        # still returns within seconds for the small payloads tools produce.
        try:
            resp = await backend._client.messages.create(**kwargs)
        except APIError as exc:
            yield {"type": "error", "content": f"anthropic error: {exc}", "meta": {}}
            yield {"type": "done", "content": "", "meta": {}}
            return

        tool_uses: list[dict] = []
        text_pieces: list[str] = []
        for block in resp.content:
            if block.type == "text":
                text_pieces.append(block.text)
            elif block.type == "tool_use":
                tool_uses.append({"id": block.id, "name": block.name, "input": block.input or {}})

        # If the model produced no tool calls, this is the final turn.
        if not tool_uses:
            text = "".join(text_pieces)
            if text:
                yield {
                    "type": "text",
                    "content": text,
                    "meta": {"model": backend._config.model},
                }
            yield {
                "type": "done",
                "content": "",
                "meta": {"model": backend._config.model, "turns": turn + 1},
            }
            return

        # Pre-text from the model BEFORE its tool calls (rare but happens).
        if text_pieces:
            yield {
                "type": "text",
                "content": "".join(text_pieces),
                "meta": {"model": backend._config.model},
            }

        # Append the assistant's full content (text + tool_use blocks) so the
        # next turn has the matching tool_use_id for each tool_result.
        chat.append({"role": "assistant", "content": resp.content})

        # Run all requested skills concurrently. Each skill is independent.
        async def _exec(tu: dict) -> tuple[dict, SkillResult]:
            skill = skills_map.get(tu["name"])
            if skill is None:
                return tu, SkillResult(ok=False, error=f"unknown skill {tu['name']!r}")
            return tu, await _run_skill(skill, tu["input"], ctx)

        results = await asyncio.gather(*[_exec(tu) for tu in tool_uses])

        tool_result_blocks: list[dict] = []
        for tu, res in results:
            yield {
                "type": "tool_call",
                "content": "",
                "meta": {
                    "name": tu["name"],
                    "input": tu["input"],
                    "ok": res.ok,
                    "result": res.content[:2000] if res.ok else None,
                    "error": res.error,
                    "artifacts": res.artifacts,
                },
            }
            tool_result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": res.content if res.ok else f"error: {res.error}",
                    "is_error": not res.ok,
                }
            )

        chat.append({"role": "user", "content": tool_result_blocks})

    # MAX_TURNS exhausted without a final text.
    yield {
        "type": "error",
        "content": f"tool loop hit max turns ({MAX_TURNS}) without a final answer",
        "meta": {},
    }
    yield {"type": "done", "content": "", "meta": {}}


async def _run_openai(
    backend,
    messages: list[Message],
    options: BackendOptions,
    *,
    session_id: str,
    session_sensitivity: Sensitivity,
    skills_map: dict[str, Skill],
) -> AsyncIterator[BackendChunk]:
    """Chat-completions tool loop for OpenAI-wire-format backends (openai,
    mistral, groq, openrouter, gemini). Same shape as the Anthropic loop:
    non-streaming turns while tools are in play (we need complete tool_calls
    before we can run a skill), hard cap at MAX_TURNS.

    OpenAI function names disallow the dots every Yagami skill name uses -
    tool definitions go out sanitized ('.' -> '__') and `name_map` resolves
    each tool_call back to the real skill. See skills/adapters.py.
    """
    import json as _json

    from openai import APIError as OpenAIAPIError

    from ..skills.adapters import openai_name_map, to_openai_tools

    tools = to_openai_tools(list(skills_map.values()))
    name_map = openai_name_map(list(skills_map.values()))

    # OpenAIBackend keeps its settings on a config object; the
    # OpenAICompatBackend subclasses inline them. Resolve either shape here
    # rather than forcing a common attribute onto the (deliberately simple)
    # backend classes.
    model = getattr(backend, "_model", None) or backend._config.model
    max_tokens_default = getattr(backend, "_max_tokens", None) or backend._config.max_tokens

    system_parts = [m.content for m in messages if m.role == "system"]
    if options.system_prompt:
        system_parts = [options.system_prompt]

    chat: list[dict] = []
    if system_parts:
        chat.append({"role": "system", "content": "\n\n".join(system_parts)})
    for m in messages:
        if m.role not in ("user", "assistant"):
            continue
        if m.images:
            content: list[dict] = []
            for img in m.images:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{img.media_type};base64,{img.data_b64}"},
                    }
                )
            if m.content:
                content.append({"type": "text", "text": m.content})
            chat.append({"role": m.role, "content": content})
        else:
            chat.append({"role": m.role, "content": m.content})

    ctx = SkillContext(session_id=session_id, session_sensitivity=session_sensitivity)

    for turn in range(MAX_TURNS):
        try:
            resp = await backend._client.chat.completions.create(
                model=model,
                messages=chat,  # type: ignore[arg-type]
                max_tokens=options.max_tokens or max_tokens_default,
                temperature=options.temperature,
                tools=tools,  # type: ignore[arg-type]
            )
        except OpenAIAPIError as exc:
            yield {"type": "error", "content": f"{backend.name} error: {exc}", "meta": {}}
            yield {"type": "done", "content": "", "meta": {}}
            return

        message = resp.choices[0].message
        tool_calls = message.tool_calls or []

        if not tool_calls:
            text = message.content or ""
            if text:
                yield {"type": "text", "content": text, "meta": {"model": model}}
            yield {"type": "done", "content": "", "meta": {"model": model, "turns": turn + 1}}
            return

        if message.content:
            yield {"type": "text", "content": message.content, "meta": {"model": model}}

        # Echo the assistant turn (with its tool_calls) so each role="tool"
        # result below can reference its tool_call_id.
        chat.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            }
        )

        async def _exec(tc) -> tuple[object, str, dict, SkillResult]:
            real_name = name_map.get(tc.function.name, tc.function.name)
            skill = skills_map.get(real_name)
            try:
                args = _json.loads(tc.function.arguments or "{}")
            except ValueError:
                return tc, real_name, {}, SkillResult(ok=False, error="malformed tool arguments")
            if skill is None:
                return tc, real_name, args, SkillResult(ok=False, error=f"unknown skill {real_name!r}")
            return tc, real_name, args, await _run_skill(skill, args, ctx)

        results = await asyncio.gather(*[_exec(tc) for tc in tool_calls])

        for tc, real_name, args, res in results:
            yield {
                "type": "tool_call",
                "content": "",
                "meta": {
                    "name": real_name,
                    "input": args,
                    "ok": res.ok,
                    "result": res.content[:2000] if res.ok else None,
                    "error": res.error,
                    "artifacts": res.artifacts,
                },
            }
            chat.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": res.content if res.ok else f"error: {res.error}",
                }
            )

    yield {
        "type": "error",
        "content": f"tool loop hit max turns ({MAX_TURNS}) without a final answer",
        "meta": {},
    }
    yield {"type": "done", "content": "", "meta": {}}
