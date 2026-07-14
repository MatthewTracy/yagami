from __future__ import annotations

import base64
import binascii
import json
import re
import time
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..auth import Principal, require_scope
from ..backends.base import ImageAttachment, Message
from ..gateway import GatewayError, GatewayRequestOptions, PolicyDeniedError
from ..governance import TransformationError, TransformationSession
from ..policy import PolicyContext, replay_decisions
from ..router.schema import Sensitivity

router = APIRouter(prefix="/v1", tags=["OpenAI compatibility"])
_gateway_invoke = require_scope("gateway:invoke")
_gateway_read = require_scope("gateway:read")
_policy_read = require_scope("policy:read")
_policy_preview = require_scope("policy:preview")
_policy_replay = require_scope("policy:replay")
_privacy_transform = require_scope("privacy:transform")
_audit_read = require_scope("audit:read")
_tool_approve = require_scope("tools:approve")


class ImageURL(BaseModel):
    url: str


class ContentPart(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["text", "image_url", "input_text", "input_image"]
    text: str | None = None
    image_url: ImageURL | str | None = None


class OpenAIMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str | list[ContentPart] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "yagami-auto"
    messages: list[OpenAIMessage] = Field(min_length=1)
    stream: bool = False
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1)
    max_completion_tokens: int | None = Field(default=None, ge=1)
    n: int = Field(default=1, ge=1)
    user: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None

    @field_validator("metadata")
    @classmethod
    def metadata_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 32:
            raise ValueError("metadata supports at most 32 keys")
        return value

    @field_validator("tools")
    @classmethod
    def tools_are_bounded(cls, value: list[dict[str, Any]] | None):
        return _validate_function_tools(value)


class ResponsesRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "yagami-auto"
    input: str | list[OpenAIMessage]
    instructions: str | None = None
    stream: bool = False
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=1)
    user: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None

    @field_validator("tools")
    @classmethod
    def tools_are_bounded(cls, value: list[dict[str, Any]] | None):
        return _validate_function_tools(value)


class PolicyPreviewRequest(BaseModel):
    model: str = "yagami-auto"
    messages: list[OpenAIMessage] = Field(min_length=1)
    user: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)
    tools: list[dict[str, Any]] | None = None

    @field_validator("tools")
    @classmethod
    def tools_are_bounded(cls, value: list[dict[str, Any]] | None):
        return _validate_function_tools(value)


class PrivacyTransformRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1_000_000)
    mode: Literal["tokenize", "redact"] = "tokenize"


class PrivacyRehydrateRequest(BaseModel):
    tokenization_id: str = Field(pattern=r"^tok_[a-f0-9]{32}$")
    text: str = Field(min_length=1, max_length=1_000_000)
    delete: bool = True


class PolicyReplayRequest(BaseModel):
    decision_ids: list[int] = Field(min_length=1, max_length=100)

    @field_validator("decision_ids")
    @classmethod
    def unique_positive_ids(cls, value: list[int]) -> list[int]:
        if any(decision_id <= 0 for decision_id in value):
            raise ValueError("decision IDs must be positive")
        return list(dict.fromkeys(value))


class ToolApprovalRequest(BaseModel):
    tools: list[str] = Field(min_length=1, max_length=100)
    purpose: str | None = Field(default=None, min_length=1, max_length=64)
    ticket: str | None = Field(default=None, min_length=1, max_length=128)
    ttl_seconds: int = Field(default=900, ge=60, le=86_400)

    @field_validator("tools")
    @classmethod
    def valid_tool_patterns(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(tool.strip() for tool in value if tool.strip()))
        if not normalized:
            raise ValueError("at least one non-empty tool pattern is required")
        if any(len(tool) > 128 for tool in normalized):
            raise ValueError("tool patterns are limited to 128 characters")
        return normalized


def _openai_error(error: GatewayError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={
            "error": {
                "message": error.message,
                "type": "invalid_request_error" if error.status_code < 500 else "api_error",
                "param": error.param,
                "code": error.code,
            }
        },
    )


def _validate_function_tools(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    if tools is None:
        return None
    if len(tools) > 64:
        raise ValueError("at most 64 function tools are supported")
    if len(json.dumps(tools, separators=(",", ":"))) > 262_144:
        raise ValueError("tool definitions exceed the 256 KiB limit")
    seen: set[str] = set()
    for tool in tools:
        function = tool.get("function")
        if tool.get("type") != "function" or not isinstance(function, dict):
            raise ValueError("only OpenAI function tools are supported")
        name = function.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", name):
            raise ValueError("function tool names must be 1-128 safe identifier characters")
        if name in seen:
            raise ValueError(f"duplicate function tool name {name!r}")
        seen.add(name)
        parameters = function.get("parameters", {"type": "object"})
        if not isinstance(parameters, dict):
            raise ValueError(f"parameters for function tool {name!r} must be an object")
    return tools


def _decode_data_url(url: str) -> ImageAttachment:
    if not url.startswith("data:image/") or ";base64," not in url:
        raise GatewayError(
            "only base64 data URLs are accepted for image inputs; remote URLs are not fetched",
            code="unsupported_image_url",
            param="messages.content.image_url",
        )
    header, encoded = url.split(",", 1)
    media_type = header[5:].split(";", 1)[0]
    try:
        base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise GatewayError("invalid base64 image data", code="invalid_image") from exc
    try:
        return ImageAttachment(media_type=media_type, data_b64=encoded)  # type: ignore[arg-type]
    except ValueError as exc:
        raise GatewayError(str(exc), code="invalid_image") from exc


def _convert_messages(messages: list[OpenAIMessage]) -> list[Message]:
    converted: list[Message] = []
    for message in messages:
        role = "system" if message.role == "developer" else message.role
        if message.content is None:
            converted.append(
                Message(
                    role=role,  # type: ignore[arg-type]
                    content="",
                    tool_call_id=message.tool_call_id,
                    name=message.name,
                    tool_calls=message.tool_calls,
                )
            )
            continue
        if isinstance(message.content, str):
            converted.append(
                Message(
                    role=role,  # type: ignore[arg-type]
                    content=message.content,
                    tool_call_id=message.tool_call_id,
                    name=message.name,
                    tool_calls=message.tool_calls,
                )
            )
            continue
        texts: list[str] = []
        images: list[ImageAttachment] = []
        for part in message.content:
            if part.type in {"text", "input_text"}:
                if part.text:
                    texts.append(part.text)
                continue
            image_value = part.image_url
            if isinstance(image_value, ImageURL):
                image_value = image_value.url
            if isinstance(image_value, str):
                images.append(_decode_data_url(image_value))
                continue
            raise GatewayError("image content is missing image_url", code="invalid_image")
        converted.append(
            Message(role=role, content="\n".join(texts), images=images or None)  # type: ignore[arg-type]
        )
    return converted


def _tool_names(tools: list[dict[str, Any]] | None) -> list[str]:
    names: list[str] = []
    for tool in tools or []:
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            name = tool["function"].get("name")
            if isinstance(name, str) and name:
                names.append(name)
        elif isinstance(tool.get("name"), str):
            names.append(tool["name"])
    return names


def _policy_context(
    *,
    principal: Principal,
    metadata: dict[str, Any],
    user: str | None,
    tools: list[dict[str, Any]] | None,
) -> PolicyContext:
    reserved = {
        "subject_id",
        "purpose",
        "jurisdiction",
        "session_id",
        "sensitivity",
        "approved_tools",
        "approval_tokens",
    }
    sensitivity = metadata.get("sensitivity")
    try:
        sensitivity_hint = Sensitivity(sensitivity) if sensitivity is not None else None
    except (TypeError, ValueError) as exc:
        raise GatewayError(
            "metadata.sensitivity must be one of none, phi, phi_medical, or secret",
            code="invalid_metadata",
            param="metadata.sensitivity",
        ) from exc
    safe_metadata = {
        str(key): value
        for key, value in metadata.items()
        if key not in reserved and isinstance(value, (str, int, float, bool, type(None)))
    }
    if metadata.get("approved_tools"):
        raise GatewayError(
            "metadata.approved_tools is not trusted; use one-time approval_tokens",
            code="invalid_tool_approval",
            status_code=403,
            param="metadata.approved_tools",
        )
    try:
        return PolicyContext(
            project_id=principal.project_id,
            subject_id=str(metadata.get("subject_id") or user or "") or None,
            purpose=str(metadata.get("purpose") or "general"),
            jurisdiction=(
                str(metadata["jurisdiction"]) if metadata.get("jurisdiction") is not None else None
            ),
            session_id=(
                str(metadata["session_id"]) if metadata.get("session_id") is not None else None
            ),
            sensitivity_hint=sensitivity_hint,
            requested_tools=_tool_names(tools),
            approval_tokens=(
                [str(token) for token in metadata.get("approval_tokens", [])]
                if isinstance(metadata.get("approval_tokens", []), list)
                else []
            ),
            metadata=safe_metadata,
        )
    except ValidationError as exc:
        raise GatewayError(
            "request metadata does not satisfy Yagami policy context constraints",
            code="invalid_metadata",
            param="metadata",
        ) from exc


def _options(
    *,
    temperature: float,
    max_tokens: int | None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
) -> GatewayRequestOptions:
    return GatewayRequestOptions(
        temperature=temperature,
        max_tokens=max_tokens or 2048,
        tools=tools,
        tool_choice=tool_choice,
    )


def _headers(prepared) -> dict[str, str]:
    return {
        "x-yagami-request-id": prepared.request_id,
        "x-yagami-decision-id": str(prepared.decision_id),
        "x-yagami-backend": prepared.decision.backend.name,
        "x-yagami-policy-hash": prepared.policy.policy_hash,
    }


@router.get("/models")
async def list_models(request: Request, _principal: Principal = Depends(_gateway_read)) -> dict:
    runtime = request.app.state.runtime
    created = int(time.time())
    rows = [{"id": "yagami-auto", "object": "model", "created": created, "owned_by": "yagami"}]
    rows.extend(
        {
            "id": backend.name,
            "object": "model",
            "created": created,
            "owned_by": "local" if backend.is_local else "provider",
        }
        for backend in runtime.backends.values()
    )
    return {"object": "list", "data": rows}


@router.post("/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest,
    request: Request,
    principal: Principal = Depends(_gateway_invoke),
):
    if body.n != 1:
        return _openai_error(
            GatewayError("only n=1 is supported", code="unsupported_parameter", param="n")
        )
    runtime = request.app.state.runtime
    try:
        prepared = await runtime.gateway.prepare(
            messages=_convert_messages(body.messages),
            model=body.model,
            context=_policy_context(
                principal=principal,
                metadata=body.metadata,
                user=body.user,
                tools=body.tools,
            ),
            options=_options(
                temperature=body.temperature,
                max_tokens=body.max_completion_tokens or body.max_tokens,
                tools=body.tools,
                tool_choice=body.tool_choice,
            ),
        )
    except GatewayError as exc:
        return _openai_error(exc)

    created = int(time.time())
    response_id = "chatcmpl-" + prepared.request_id.removeprefix("ygm_")
    if body.stream:

        async def events():
            first = True
            saw_tool_call = False
            async for chunk in runtime.gateway.stream(prepared):
                if chunk["type"] == "text":
                    delta: dict[str, Any] = {"content": chunk["content"]}
                    if first:
                        delta["role"] = "assistant"
                        first = False
                    payload = {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": prepared.decision.backend.name,
                        "system_fingerprint": prepared.policy.policy_hash[:24],
                        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                    }
                    yield "data: " + json.dumps(payload, separators=(",", ":")) + "\n\n"
                elif (
                    chunk["type"] == "tool_call"
                    and chunk.get("meta", {}).get("kind") == "caller_function"
                ):
                    meta = chunk["meta"]
                    function_delta = {
                        key: value
                        for key, value in {
                            "name": meta.get("name"),
                            "arguments": meta.get("arguments"),
                        }.items()
                        if value is not None
                    }
                    tool_delta = {
                        "index": int(meta.get("index") or 0),
                        "type": "function",
                        "function": function_delta,
                    }
                    if meta.get("id") is not None:
                        tool_delta["id"] = meta["id"]
                    delta = {"tool_calls": [tool_delta]}
                    if first:
                        delta["role"] = "assistant"
                        first = False
                    saw_tool_call = True
                    payload = {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": prepared.decision.backend.name,
                        "system_fingerprint": prepared.policy.policy_hash[:24],
                        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                    }
                    yield "data: " + json.dumps(payload, separators=(",", ":")) + "\n\n"
                elif chunk["type"] == "error":
                    yield (
                        "data: "
                        + json.dumps(
                            {"error": {"message": chunk["content"], "type": "api_error"}},
                            separators=(",", ":"),
                        )
                        + "\n\n"
                    )
            final = {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": prepared.decision.backend.name,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "tool_calls" if saw_tool_call else "stop",
                    }
                ],
            }
            yield "data: " + json.dumps(final, separators=(",", ":")) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            events(), media_type="text/event-stream", headers=_headers(prepared)
        )

    try:
        result = await runtime.gateway.execute(prepared)
    except GatewayError as exc:
        return _openai_error(exc)
    return JSONResponse(
        headers=_headers(prepared),
        content={
            "id": response_id,
            "object": "chat.completion",
            "created": created,
            "model": result.backend,
            "system_fingerprint": result.policy.policy_hash[:24],
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": result.text or None,
                        "refusal": None,
                        "tool_calls": result.tool_calls or None,
                    },
                    "finish_reason": "tool_calls" if result.tool_calls else "stop",
                }
            ],
            "usage": {
                "prompt_tokens": result.input_tokens,
                "completion_tokens": result.output_tokens,
                "total_tokens": result.input_tokens + result.output_tokens,
            },
            "yagami": {
                "decision_id": result.decision_id,
                "policy": result.policy.passport(),
            },
        },
    )


def _responses_messages(body: ResponsesRequest) -> list[OpenAIMessage]:
    messages = (
        [OpenAIMessage(role="user", content=body.input)]
        if isinstance(body.input, str)
        else list(body.input)
    )
    if body.instructions:
        messages.insert(0, OpenAIMessage(role="developer", content=body.instructions))
    return messages


def _response_object(*, response_id: str, created: int, result, metadata: dict) -> dict:
    message_id = "msg_" + uuid4().hex
    return {
        "id": response_id,
        "object": "response",
        "created_at": created,
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "model": result.backend,
        "output": [
            {
                "id": message_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": result.text, "annotations": [], "logprobs": []}
                ],
            }
        ],
        "parallel_tool_calls": False,
        "temperature": None,
        "tool_choice": "auto",
        "tools": [],
        "metadata": metadata,
        "usage": {
            "input_tokens": result.input_tokens,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": result.output_tokens,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": result.input_tokens + result.output_tokens,
        },
        "yagami": {"decision_id": result.decision_id, "policy": result.policy.passport()},
    }


@router.post("/responses")
async def create_response(
    body: ResponsesRequest,
    request: Request,
    principal: Principal = Depends(_gateway_invoke),
):
    if body.tools:
        return _openai_error(
            GatewayError(
                "caller-defined tools are not yet supported on this compatibility endpoint",
                code="unsupported_parameter",
                param="tools",
            )
        )
    runtime = request.app.state.runtime
    try:
        prepared = await runtime.gateway.prepare(
            messages=_convert_messages(_responses_messages(body)),
            model=body.model,
            context=_policy_context(
                principal=principal,
                metadata=body.metadata,
                user=body.user,
                tools=body.tools,
            ),
            options=_options(
                temperature=body.temperature,
                max_tokens=body.max_output_tokens,
            ),
        )
    except GatewayError as exc:
        return _openai_error(exc)
    created = int(time.time())
    response_id = "resp_" + prepared.request_id.removeprefix("ygm_")

    if body.stream:

        async def events():
            sequence = 0
            created_event = {
                "type": "response.created",
                "sequence_number": sequence,
                "response": {
                    "id": response_id,
                    "object": "response",
                    "created_at": created,
                    "status": "in_progress",
                    "model": prepared.decision.backend.name,
                    "output": [],
                    "metadata": body.metadata,
                },
            }
            yield "data: " + json.dumps(created_event, separators=(",", ":")) + "\n\n"
            text_parts: list[str] = []
            async for chunk in runtime.gateway.stream(prepared):
                if chunk["type"] == "text":
                    text_parts.append(chunk["content"])
                    sequence += 1
                    event = {
                        "type": "response.output_text.delta",
                        "sequence_number": sequence,
                        "item_id": "msg_" + prepared.request_id.removeprefix("ygm_"),
                        "output_index": 0,
                        "content_index": 0,
                        "delta": chunk["content"],
                    }
                    yield "data: " + json.dumps(event, separators=(",", ":")) + "\n\n"
                elif chunk["type"] == "error":
                    sequence += 1
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "type": "error",
                                "sequence_number": sequence,
                                "error": {"message": chunk["content"], "type": "api_error"},
                            },
                            separators=(",", ":"),
                        )
                        + "\n\n"
                    )
            sequence += 1
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "response.output_text.done",
                        "sequence_number": sequence,
                        "item_id": "msg_" + prepared.request_id.removeprefix("ygm_"),
                        "output_index": 0,
                        "content_index": 0,
                        "text": "".join(text_parts),
                    },
                    separators=(",", ":"),
                )
                + "\n\n"
            )
            sequence += 1
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "response.completed",
                        "sequence_number": sequence,
                        "response": {
                            "id": response_id,
                            "object": "response",
                            "created_at": created,
                            "status": "completed",
                            "model": prepared.decision.backend.name,
                            "output": [],
                            "metadata": body.metadata,
                        },
                    },
                    separators=(",", ":"),
                )
                + "\n\n"
            )
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            events(), media_type="text/event-stream", headers=_headers(prepared)
        )

    try:
        result = await runtime.gateway.execute(prepared)
    except GatewayError as exc:
        return _openai_error(exc)
    return JSONResponse(
        headers=_headers(prepared),
        content=_response_object(
            response_id=response_id,
            created=created,
            result=result,
            metadata=body.metadata,
        ),
    )


@router.get("/policy")
async def get_policy(request: Request, _principal: Principal = Depends(_policy_read)) -> dict:
    engine = request.app.state.runtime.policy_engine
    return {
        "policy": engine.document.model_dump(mode="json"),
        "policy_hash": engine.policy_hash,
    }


@router.post("/policy/preview")
async def preview_policy(
    body: PolicyPreviewRequest,
    request: Request,
    principal: Principal = Depends(_policy_preview),
):
    runtime = request.app.state.runtime
    try:
        prepared = await runtime.gateway.prepare(
            messages=_convert_messages(body.messages),
            model=body.model,
            context=_policy_context(
                principal=principal,
                metadata=body.metadata,
                user=body.user,
                tools=body.tools,
            ),
            options=GatewayRequestOptions(),
            persist=False,
        )
    except PolicyDeniedError as exc:
        return {
            "allowed": False,
            "policy": exc.policy.passport(),
            "reason": exc.message,
        }
    except GatewayError as exc:
        return _openai_error(exc)
    return {
        "allowed": not (prepared.policy.denied and prepared.policy.mode.value == "enforce"),
        "shadow_would_allow": not prepared.policy.denied,
        "backend": prepared.decision.backend.name,
        "is_local": prepared.decision.backend.is_local,
        "routing_reason": prepared.decision.reason,
        "classification": prepared.decision.classification,
        "policy": prepared.policy.passport(),
    }


@router.post("/privacy/transform")
async def privacy_transform(
    body: PrivacyTransformRequest,
    request: Request,
    principal: Principal = Depends(_privacy_transform),
):
    runtime = request.app.state.runtime
    if body.mode == "tokenize" and not runtime.transformer.tokenization_available:
        return _openai_error(
            GatewayError(
                "tokenization requires YAGAMI_TRANSFORM_KEY; run yagami-keygen to create one",
                code="transform_key_unavailable",
                status_code=503,
            )
        )
    tokenization_id = "tok_" + uuid4().hex
    session = TransformationSession(
        request_id=tokenization_id,
        project_id=principal.project_id,
        mode=body.mode,
    )
    try:
        transformed = await runtime.transformer.transform_text(body.text, session=session)
    except TransformationError as exc:
        return _openai_error(GatewayError(str(exc), code="transformation_failed", status_code=422))
    await runtime.gateway.append_audit(
        project_id=principal.project_id,
        request_id=tokenization_id,
        event_type="privacy.transformed",
        payload={
            "mode": body.mode,
            "entity_counts": session.summary().get("entity_counts", {}),
            "rehydratable": bool(session.mapping),
        },
    )
    return {
        "object": "yagami.privacy_transformation",
        "tokenization_id": tokenization_id if session.mapping else None,
        "mode": body.mode,
        "text": transformed,
        "rehydratable": bool(session.mapping),
        "expires_in": runtime.settings.transform_vault_ttl_seconds if session.mapping else None,
        "manifest": session.summary(),
    }


@router.post("/policy/replay")
async def policy_replay(
    body: PolicyReplayRequest,
    request: Request,
    principal: Principal = Depends(_policy_replay),
):
    runtime = request.app.state.runtime
    rows = await replay_decisions(
        engine=runtime.policy_engine,
        project_id=principal.project_id,
        decision_ids=body.decision_ids,
    )
    found = {row["decision_id"] for row in rows}
    await runtime.gateway.append_audit(
        project_id=principal.project_id,
        event_type="policy.replayed",
        payload={
            "decision_ids": sorted(found),
            "not_found_count": len(body.decision_ids) - len(found),
            "policy_hash": runtime.policy_engine.policy_hash,
        },
    )
    return {
        "object": "yagami.policy_replay",
        "policy_hash": runtime.policy_engine.policy_hash,
        "results": rows,
        "not_found": [decision_id for decision_id in body.decision_ids if decision_id not in found],
    }


@router.post("/privacy/rehydrate")
async def privacy_rehydrate(
    body: PrivacyRehydrateRequest,
    request: Request,
    principal: Principal = Depends(_privacy_transform),
):
    runtime = request.app.state.runtime
    try:
        text = await runtime.transformer.rehydrate_from_vault(
            body.text,
            request_id=body.tokenization_id,
            project_id=principal.project_id,
            delete=body.delete,
        )
    except TransformationError as exc:
        return _openai_error(GatewayError(str(exc), code="rehydration_failed", status_code=404))
    await runtime.gateway.append_audit(
        project_id=principal.project_id,
        request_id=body.tokenization_id,
        event_type="privacy.rehydrated",
        payload={"mapping_deleted": body.delete},
    )
    return {
        "object": "yagami.privacy_rehydration",
        "tokenization_id": body.tokenization_id,
        "text": text,
        "deleted": body.delete,
    }


@router.get("/audit/verify")
async def audit_verify(
    request: Request,
    principal: Principal = Depends(_audit_read),
) -> dict:
    """Verify the authenticated project's complete audit hash chain."""
    return await request.app.state.runtime.audit.verify(principal.project_id)


@router.get("/audit/events")
async def audit_events(
    request: Request,
    limit: int = Query(default=10_000, ge=1, le=100_000),
    principal: Principal = Depends(_audit_read),
):
    """Export project-scoped, content-free audit evidence as NDJSON."""
    payload = await request.app.state.runtime.audit.export_ndjson(
        principal.project_id,
        limit=limit,
    )
    return StreamingResponse(
        iter([payload]),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="yagami-audit.ndjson"'},
    )


@router.post("/tool-approvals", status_code=201)
async def create_tool_approval(
    body: ToolApprovalRequest,
    request: Request,
    principal: Principal = Depends(_tool_approve),
) -> dict:
    """Issue a one-time capability; its plaintext token is returned only once."""
    runtime = request.app.state.runtime
    grant = await runtime.approvals.create(
        project_id=principal.project_id,
        tools=body.tools,
        purpose=body.purpose,
        ticket=body.ticket,
        created_by=principal.key_fingerprint,
        ttl_seconds=body.ttl_seconds,
    )
    await runtime.gateway.append_audit(
        project_id=principal.project_id,
        event_type="tool_approval.created",
        payload={
            "approval_id": grant.id,
            "tools": grant.tools,
            "purpose": grant.purpose,
            "ticket": grant.ticket,
            "expires_at": grant.expires_at,
            "created_by": principal.key_fingerprint,
        },
    )
    return {
        "object": "yagami.tool_approval",
        "id": grant.id,
        "token": grant.token,
        "project_id": grant.project_id,
        "tools": grant.tools,
        "purpose": grant.purpose,
        "ticket": grant.ticket,
        "created_at": grant.created_at,
        "expires_at": grant.expires_at,
        "status": "active",
    }


@router.get("/tool-approvals")
async def list_tool_approvals(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1_000),
    principal: Principal = Depends(_tool_approve),
) -> dict:
    rows = await request.app.state.runtime.approvals.list(principal.project_id, limit=limit)
    return {"object": "list", "data": rows}


@router.delete("/tool-approvals/{approval_id}")
async def revoke_tool_approval(
    approval_id: str,
    request: Request,
    principal: Principal = Depends(_tool_approve),
):
    if not approval_id.startswith("apr_") or len(approval_id) != 36:
        return _openai_error(
            GatewayError("invalid approval ID", code="invalid_tool_approval", param="approval_id")
        )
    runtime = request.app.state.runtime
    revoked = await runtime.approvals.revoke(
        project_id=principal.project_id,
        approval_id=approval_id,
    )
    if not revoked:
        return _openai_error(
            GatewayError(
                "active tool approval not found",
                code="tool_approval_not_found",
                status_code=404,
            )
        )
    await runtime.gateway.append_audit(
        project_id=principal.project_id,
        event_type="tool_approval.revoked",
        payload={"approval_id": approval_id, "revoked_by": principal.key_fingerprint},
    )
    return {"id": approval_id, "object": "yagami.tool_approval", "deleted": True}
