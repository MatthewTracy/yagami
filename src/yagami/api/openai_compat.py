from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
import struct
import time
from typing import Any, Literal, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..auth import Principal, require_scope
from ..backends.base import Capability, ImageAttachment, Message
from ..gateway import GatewayError, GatewayRequestOptions, PolicyDeniedError
from ..governance import TransformationError, TransformationSession
from ..policy import PolicyContext, PolicyMode, RoutePolicy, TransformPolicy, replay_decisions
from ..projects import ProjectLimitError
from ..router.schema import DataLabel, Sensitivity
from ..telemetry.costs import rough_token_count
from ..telemetry.decisions import persist_decision
from ..responses import (
    ResponseNotFoundError,
    append_response_event,
    complete_response_job,
    create_response_job,
    fail_response_job,
    get_response_context,
    get_response_job,
    list_response_events,
    request_response_cancel,
    response_cancel_requested,
    set_response_status,
)

router = APIRouter(prefix="/v1", tags=["OpenAI compatibility"])
log = logging.getLogger("yagami.openai_compat")
_MAX_MESSAGE_CHARS = 1_000_000
_MAX_REQUEST_TEXT_CHARS = 4_000_000
_MAX_MESSAGES = 256
_MAX_CONTENT_PARTS = 64
_MAX_METADATA_BYTES = 16_384
_gateway_invoke = require_scope("gateway:invoke")
_gateway_read = require_scope("gateway:read")
_policy_read = require_scope("policy:read")
_policy_preview = require_scope("policy:preview")
_policy_replay = require_scope("policy:replay")
_privacy_transform = require_scope("privacy:transform")
_audit_read = require_scope("audit:read")
_audit_manage = require_scope("audit:manage")
_tool_approve = require_scope("tools:approve")
_response_tasks: dict[str, asyncio.Task[None]] = {}


async def shutdown_response_jobs() -> None:
    tasks = list(_response_tasks.values())
    _response_tasks.clear()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


class ImageURL(BaseModel):
    url: str = Field(min_length=1, max_length=28_000_000)


class ContentPart(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["text", "image_url", "input_text", "input_image"]
    text: str | None = Field(default=None, max_length=_MAX_MESSAGE_CHARS)
    image_url: ImageURL | str | None = None


class OpenAIMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str | list[ContentPart] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None

    @model_validator(mode="after")
    def content_is_bounded(self):
        if isinstance(self.content, str) and len(self.content) > _MAX_MESSAGE_CHARS:
            raise ValueError("message content exceeds 1,000,000 characters")
        if isinstance(self.content, list) and len(self.content) > _MAX_CONTENT_PARTS:
            raise ValueError("message content supports at most 64 parts")
        if self.tool_calls is not None:
            if len(self.tool_calls) > 64:
                raise ValueError("message supports at most 64 tool calls")
            if len(json.dumps(self.tool_calls, separators=(",", ":"))) > 262_144:
                raise ValueError("message tool calls exceed 256 KiB")
        return self


def _messages_text_size(messages: list[OpenAIMessage]) -> int:
    total = 0
    for message in messages:
        if isinstance(message.content, str):
            total += len(message.content)
        elif isinstance(message.content, list):
            total += sum(len(part.text or "") for part in message.content)
    return total


def _validate_metadata(value: dict[str, Any]) -> dict[str, Any]:
    if len(value) > 32:
        raise ValueError("metadata supports at most 32 keys")
    if len(json.dumps(value, separators=(",", ":"), default=str)) > _MAX_METADATA_BYTES:
        raise ValueError("metadata exceeds 16 KiB")
    return value


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(default="yagami-auto", min_length=1, max_length=128)
    messages: list[OpenAIMessage] = Field(min_length=1, max_length=_MAX_MESSAGES)
    stream: bool = False
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=131_072)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=131_072)
    n: int = Field(default=1, ge=1, le=1)
    user: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None

    @field_validator("metadata")
    @classmethod
    def metadata_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value)

    @field_validator("tools")
    @classmethod
    def tools_are_bounded(cls, value: list[dict[str, Any]] | None):
        return _validate_function_tools(value)

    @model_validator(mode="after")
    def aggregate_text_is_bounded(self):
        if _messages_text_size(self.messages) > _MAX_REQUEST_TEXT_CHARS:
            raise ValueError("aggregate message text exceeds 4,000,000 characters")
        return self


class ResponsesFunctionCall(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["function_call"]
    call_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    arguments: str = Field(default="{}", max_length=262_144)


class ResponsesFunctionCallOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["function_call_output"]
    call_id: str = Field(min_length=1, max_length=128)
    output: str | list[ContentPart]


ResponsesInputItem = OpenAIMessage | ResponsesFunctionCall | ResponsesFunctionCallOutput


class ResponsesRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(default="yagami-auto", min_length=1, max_length=128)
    input: str | list[ResponsesInputItem]
    instructions: str | None = Field(default=None, max_length=_MAX_MESSAGE_CHARS)
    stream: bool = False
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=1, le=131_072)
    user: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    parallel_tool_calls: bool = True
    background: bool = False
    store: bool = True
    previous_response_id: str | None = Field(default=None, pattern=r"^resp_[A-Za-z0-9_-]{8,128}$")
    conversation: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("tools")
    @classmethod
    def tools_are_bounded(cls, value: list[dict[str, Any]] | None):
        return _validate_response_tools(value)

    @field_validator("metadata")
    @classmethod
    def metadata_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value)

    @model_validator(mode="after")
    def aggregate_text_is_bounded(self):
        if self.background and self.stream:
            raise ValueError("background responses cannot use request-bound streaming")
        if self.background and not self.store:
            raise ValueError("background responses require store=true")
        total = len(self.instructions or "")
        if isinstance(self.input, str):
            total += len(self.input)
        else:
            if len(self.input) > _MAX_MESSAGES:
                raise ValueError("responses input supports at most 256 messages")
            messages = [item for item in self.input if isinstance(item, OpenAIMessage)]
            total += _messages_text_size(messages)
            for item in self.input:
                if isinstance(item, ResponsesFunctionCall):
                    total += len(item.arguments)
                elif isinstance(item, ResponsesFunctionCallOutput):
                    if isinstance(item.output, str):
                        total += len(item.output)
                    else:
                        total += sum(len(part.text or "") for part in item.output)
        if total > _MAX_REQUEST_TEXT_CHARS:
            raise ValueError("aggregate input text exceeds 4,000,000 characters")
        return self


class EmbeddingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(default="yagami-embedding", min_length=1, max_length=128)
    input: str | list[str]
    encoding_format: Literal["float", "base64"] = "float"
    dimensions: int | None = Field(default=None, ge=1, le=65_536)
    user: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def metadata_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value)

    @model_validator(mode="after")
    def input_is_bounded(self):
        values = [self.input] if isinstance(self.input, str) else self.input
        if not values:
            raise ValueError("embedding input cannot be empty")
        if len(values) > 2_048:
            raise ValueError("embedding input supports at most 2,048 strings")
        if any(not value for value in values):
            raise ValueError("embedding input strings cannot be empty")
        if any(len(value) > _MAX_MESSAGE_CHARS for value in values):
            raise ValueError("an embedding input exceeds 1,000,000 characters")
        if sum(len(value) for value in values) > _MAX_REQUEST_TEXT_CHARS:
            raise ValueError("aggregate embedding input exceeds 4,000,000 characters")
        return self


class PolicyPreviewRequest(BaseModel):
    model: str = Field(default="yagami-auto", min_length=1, max_length=128)
    messages: list[OpenAIMessage] = Field(min_length=1, max_length=_MAX_MESSAGES)
    user: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)
    tools: list[dict[str, Any]] | None = None

    @field_validator("tools")
    @classmethod
    def tools_are_bounded(cls, value: list[dict[str, Any]] | None):
        return _validate_function_tools(value)

    @model_validator(mode="after")
    def aggregate_text_is_bounded(self):
        if _messages_text_size(self.messages) > _MAX_REQUEST_TEXT_CHARS:
            raise ValueError("aggregate message text exceeds 4,000,000 characters")
        return self


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
    subject_id: str | None = Field(default=None, min_length=1, max_length=128)
    schema_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
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


def _validate_response_tools(
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
        if tool.get("type") != "function":
            raise ValueError("only Responses API function tools are supported")
        function = tool.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            parameters = function.get("parameters", {"type": "object"})
        else:
            name = tool.get("name")
            parameters = tool.get("parameters", {"type": "object"})
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", name):
            raise ValueError("function tool names must be 1-128 safe identifier characters")
        if name in seen:
            raise ValueError(f"duplicate function tool name {name!r}")
        seen.add(name)
        if not isinstance(parameters, dict):
            raise ValueError(f"parameters for function tool {name!r} must be an object")
    return tools


def _responses_chat_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    normalized: list[dict[str, Any]] = []
    for tool in tools or []:
        if isinstance(tool.get("function"), dict):
            normalized.append(tool)
            continue
        function: dict[str, Any] = {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters", {"type": "object"}),
        }
        if "strict" in tool:
            function["strict"] = tool["strict"]
        normalized.append({"type": "function", "function": function})
    return normalized or None


def _responses_tool_choice(choice: Any) -> Any:
    if isinstance(choice, dict) and choice.get("type") == "function":
        name = choice.get("name")
        return {"type": "function", "function": {"name": name}}
    return choice


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
        return ImageAttachment(
            media_type=cast(
                Literal["image/png", "image/jpeg", "image/gif", "image/webp"], media_type
            ),
            data_b64=encoded,
        )
    except ValueError as exc:
        raise GatewayError(str(exc), code="invalid_image") from exc


def _convert_messages(messages: list[OpenAIMessage]) -> list[Message]:
    converted: list[Message] = []
    for message in messages:
        role: Literal["system", "user", "assistant", "tool"] = (
            "system" if message.role == "developer" else message.role
        )
        if message.content is None:
            converted.append(
                Message(
                    role=role,
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
                    role=role,
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
        converted.append(Message(role=role, content="\n".join(texts), images=images or None))
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


@router.post("/embeddings")
async def create_embeddings(
    body: EmbeddingsRequest,
    request: Request,
    principal: Principal = Depends(_gateway_invoke),
):
    """Embed content only after classifying and enforcing its destination boundary."""
    runtime = request.app.state.runtime
    embedder = runtime.embedder
    if embedder is None:
        return _openai_error(
            GatewayError(
                "no embedding provider is configured",
                code="embedding_provider_unavailable",
                status_code=503,
            )
        )
    aliases = {"yagami-auto", "yagami-embedding", embedder.model}
    if body.model not in aliases:
        return _openai_error(
            GatewayError(
                f"embedding model {body.model!r} is not available",
                code="model_not_found",
                status_code=404,
                param="model",
            )
        )

    values = [body.input] if isinstance(body.input, str) else body.input
    combined = "\n".join(values)
    context = _policy_context(
        principal=principal,
        metadata=body.metadata,
        user=body.user,
        tools=None,
    )
    try:
        await runtime.governor.check_request(
            project_id=context.project_id,
            purpose=context.purpose,
            jurisdiction=context.jurisdiction,
        )
        routing = await runtime.routing_policy.decide([Message(role="user", content=combined)])
    except ProjectLimitError as exc:
        return _openai_error(
            GatewayError(
                str(exc),
                code=exc.code,
                status_code=429 if exc.code == "rate_limit_exceeded" else 403,
            )
        )
    except Exception:
        log.warning("embedding classification failed closed", exc_info=True)
        return _openai_error(
            GatewayError(
                "embedding request could not be classified safely",
                code="classification_unavailable",
                status_code=503,
            )
        )

    classification = routing.classification
    try:
        sensitivity = Sensitivity(classification.get("sensitivity", "none"))
    except ValueError:
        sensitivity = Sensitivity.SECRET
    labels = {
        DataLabel(value)
        for value in classification.get("data_labels", [])
        if value in {label.value for label in DataLabel}
    }
    provider = runtime.config.memory.embedding_provider
    zone = runtime.config.memory.embedding_trust_zone
    evaluation = runtime.policy_engine.evaluate(
        context=context,
        detected_sensitivity=sensitivity,
        candidate_backend=provider,
        data_labels=labels,
        candidate_trust_zone=zone,
        required_capabilities={Capability.EMBEDDINGS},
    )
    if evaluation.route == RoutePolicy.LOCAL and not zone.is_private:
        evaluation.denied = True
        evaluation.reasons.append("embedding destination is outside the required local boundary")
    if evaluation.route == RoutePolicy.CLOUD and zone.is_private:
        evaluation.denied = True
        evaluation.reasons.append("embedding destination does not satisfy the required cloud route")
    if evaluation.allowed_backends is not None and provider not in evaluation.allowed_backends:
        evaluation.denied = True
        evaluation.reasons.append("embedding provider is not in the policy allowlist")
    if evaluation.allowed_trust_zones is not None and zone not in evaluation.allowed_trust_zones:
        evaluation.denied = True
        evaluation.reasons.append("embedding trust zone is not in the policy allowlist")
    unsupported = set(evaluation.required_capabilities) - {Capability.EMBEDDINGS}
    if unsupported:
        evaluation.denied = True
        evaluation.reasons.append("embedding provider lacks policy-required capabilities")

    request_id = "ygm_" + uuid4().hex
    decision = {
        "backend": provider,
        "is_local": zone.is_private,
        "reason": "governed embedding destination",
        "classification": classification,
    }
    await runtime.sessions.ensure_gateway_session(
        request_id,
        project_id=context.project_id,
    )
    decision_id = await persist_decision(
        session_id=request_id,
        user_text="",
        decision=decision,
        request_id=request_id,
        project_id=context.project_id,
        channel="embeddings",
        policy_decision=evaluation.passport(),
        request_context={
            "project_id": context.project_id,
            "purpose": context.purpose,
            "jurisdiction": context.jurisdiction,
            "metadata_keys": sorted(context.metadata),
        },
    )
    await runtime.gateway.append_audit(
        project_id=context.project_id,
        request_id=request_id,
        event_type="embedding.decision",
        payload={
            "decision_id": decision_id,
            "provider": provider,
            "trust_zone": zone.value,
            "policy_hash": evaluation.policy_hash,
            "denied": evaluation.denied,
            "input_count": len(values),
        },
    )
    if evaluation.denied and evaluation.mode == PolicyMode.ENFORCE:
        return _openai_error(
            GatewayError(
                "embedding request denied by Yagami policy",
                code="policy_denied",
                status_code=403,
            )
        )

    transformed = list(values)
    if evaluation.transform != TransformPolicy.NONE:
        transform_session = TransformationSession(
            request_id=request_id,
            project_id=context.project_id,
            mode=evaluation.transform.value,
        )
        try:
            transformed = [
                await runtime.transformer.transform_text(value, session=transform_session)
                for value in values
            ]
        except TransformationError:
            return _openai_error(
                GatewayError(
                    "embedding privacy transformation could not be completed",
                    code="transformation_failed",
                    status_code=422,
                )
            )

    vectors: list[list[float]] = []
    try:
        async with runtime.governor.slot(context.project_id):
            for value in transformed:
                vector = await embedder.embed(value)
                if vector is None:
                    raise RuntimeError("embedding provider returned no vector")
                if body.dimensions is not None and len(vector) != body.dimensions:
                    return _openai_error(
                        GatewayError(
                            f"configured embedding provider returns {len(vector)} dimensions",
                            code="unsupported_dimensions",
                            param="dimensions",
                        )
                    )
                vectors.append(vector)
    except ProjectLimitError as exc:
        return _openai_error(GatewayError(str(exc), code=exc.code, status_code=429))
    except Exception:
        log.warning(
            "embedding provider failed request_id=%s provider=%s",
            request_id,
            provider,
            exc_info=True,
        )
        return _openai_error(
            GatewayError(
                "embedding provider failed",
                code="embedding_provider_error",
                status_code=502,
            )
        )

    tokens = sum(rough_token_count(value) for value in values)
    encoded_vectors: list[list[float] | str] = []
    if body.encoding_format == "base64":
        encoded_vectors.extend(
            base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode("ascii")
            for vector in vectors
        )
    else:
        encoded_vectors.extend(vectors)
    return JSONResponse(
        content={
            "object": "list",
            "data": [
                {"object": "embedding", "embedding": vector, "index": index}
                for index, vector in enumerate(encoded_vectors)
            ],
            "model": embedder.model,
            "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
        },
        headers={
            "x-yagami-request-id": request_id,
            "x-yagami-decision-id": str(decision_id),
            "x-yagami-backend": provider,
            "x-yagami-policy-hash": evaluation.policy_hash,
            "x-yagami-trust-zone": zone.value,
        },
    )


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
    if isinstance(body.input, str):
        messages = [OpenAIMessage(role="user", content=body.input)]
    else:
        messages = []
        for item in body.input:
            if isinstance(item, OpenAIMessage):
                messages.append(item)
            elif isinstance(item, ResponsesFunctionCall):
                messages.append(
                    OpenAIMessage(
                        role="assistant",
                        content=None,
                        tool_calls=[
                            {
                                "id": item.call_id,
                                "type": "function",
                                "function": {
                                    "name": item.name,
                                    "arguments": item.arguments,
                                },
                            }
                        ],
                    )
                )
            else:
                messages.append(
                    OpenAIMessage(
                        role="tool",
                        tool_call_id=item.call_id,
                        content=item.output,
                    )
                )
    if body.instructions:
        messages.insert(0, OpenAIMessage(role="developer", content=body.instructions))
    return messages


def _response_output(*, response_id: str, text: str, tool_calls: list[dict]) -> list[dict]:
    message_id = "msg_" + uuid4().hex
    output: list[dict] = []
    if text or not tool_calls:
        output.append(
            {
                "id": message_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": text, "annotations": [], "logprobs": []}
                ],
            }
        )
    for index, call in enumerate(tool_calls):
        function = call.get("function", {})
        output.append(
            {
                "id": f"fc_{response_id.removeprefix('resp_')}_{index}",
                "type": "function_call",
                "status": "completed",
                "call_id": call.get("id") or f"call_{index}",
                "name": function.get("name", ""),
                "arguments": function.get("arguments", ""),
            }
        )
    return output


def _response_object(
    *,
    response_id: str,
    created: int,
    result,
    metadata: dict,
    tools: list[dict] | None,
    tool_choice: Any,
    parallel_tool_calls: bool,
) -> dict:
    return {
        "id": response_id,
        "object": "response",
        "created_at": created,
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "model": result.backend,
        "output": _response_output(
            response_id=response_id, text=result.text, tool_calls=result.tool_calls
        ),
        "parallel_tool_calls": parallel_tool_calls,
        "temperature": None,
        "tool_choice": tool_choice or "auto",
        "tools": tools or [],
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
    runtime = request.app.state.runtime
    chat_tools = _responses_chat_tools(body.tools)
    try:
        historical_messages = await get_response_context(
            project_id=principal.project_id,
            previous_response_id=body.previous_response_id,
            conversation_id=body.conversation if body.previous_response_id is None else None,
        )
    except ResponseNotFoundError:
        return _openai_error(
            GatewayError(
                "previous response was not found for this project",
                code="response_not_found",
                status_code=404,
                param="previous_response_id",
            )
        )
    current_messages = _convert_messages(_responses_messages(body))
    policy_metadata = dict(body.metadata)
    if body.conversation and "session_id" not in policy_metadata:
        policy_metadata["session_id"] = body.conversation
    try:
        prepared = await runtime.gateway.prepare(
            messages=[*historical_messages, *current_messages],
            model=body.model,
            context=_policy_context(
                principal=principal,
                metadata=policy_metadata,
                user=body.user,
                tools=chat_tools,
            ),
            options=_options(
                temperature=body.temperature,
                max_tokens=body.max_output_tokens,
                tools=chat_tools,
                tool_choice=_responses_tool_choice(body.tool_choice),
            ),
        )
    except GatewayError as exc:
        return _openai_error(exc)
    created = int(time.time())
    response_id = "resp_" + prepared.request_id.removeprefix("ygm_")
    safe_metadata = {
        key: value
        for key, value in body.metadata.items()
        if key not in {"approval_tokens", "approved_tools"}
    }
    should_store = body.store or body.background
    if should_store:
        await create_response_job(
            response_id=response_id,
            project_id=principal.project_id,
            request_id=prepared.request_id,
            decision_id=prepared.decision_id,
            model=prepared.decision.backend.name,
            status="queued" if body.background else "in_progress",
            messages=[*historical_messages, *current_messages],
            metadata=safe_metadata,
            previous_response_id=body.previous_response_id,
            conversation_id=body.conversation,
            retention_days=prepared.policy.retention_days,
        )
        await append_response_event(
            response_id,
            0,
            {
                "type": "response.created",
                "sequence_number": 0,
                "response": {
                    "id": response_id,
                    "object": "response",
                    "created_at": created,
                    "status": "queued" if body.background else "in_progress",
                    "model": prepared.decision.backend.name,
                },
            },
        )

    if body.background:

        async def execute_background() -> None:
            try:
                await set_response_status(response_id, "in_progress")
                if await response_cancel_requested(response_id):
                    raise asyncio.CancelledError
                result = await runtime.gateway.execute(prepared)
                output = _response_object(
                    response_id=response_id,
                    created=created,
                    result=result,
                    metadata=safe_metadata,
                    tools=body.tools,
                    tool_choice=body.tool_choice,
                    parallel_tool_calls=body.parallel_tool_calls,
                )
                await complete_response_job(response_id, output)
                await append_response_event(
                    response_id,
                    1,
                    {
                        "type": "response.completed",
                        "sequence_number": 1,
                        "response": output,
                    },
                )
            except asyncio.CancelledError:
                await fail_response_job(
                    response_id,
                    status="cancelled",
                    code="response_cancelled",
                    message="response execution was cancelled",
                )
                await append_response_event(
                    response_id,
                    1,
                    {
                        "type": "response.cancelled",
                        "sequence_number": 1,
                        "response": {"id": response_id, "status": "cancelled"},
                    },
                )
            except Exception as exc:  # noqa: BLE001 - persist safe terminal state
                log.exception("background response %s failed: %s", response_id, type(exc).__name__)
                await fail_response_job(
                    response_id,
                    status="failed",
                    code="response_execution_failed",
                    message="background response execution failed",
                )
                await append_response_event(
                    response_id,
                    1,
                    {
                        "type": "response.failed",
                        "sequence_number": 1,
                        "response": {"id": response_id, "status": "failed"},
                    },
                )
            finally:
                _response_tasks.pop(response_id, None)

        task = asyncio.create_task(execute_background())
        _response_tasks[response_id] = task
        return JSONResponse(
            status_code=202,
            headers=_headers(prepared),
            content={
                "id": response_id,
                "object": "response",
                "created_at": created,
                "status": "queued",
                "model": prepared.decision.backend.name,
                "output": [],
                "metadata": safe_metadata,
                "previous_response_id": body.previous_response_id,
                "conversation": body.conversation,
            },
        )

    if body.stream:

        async def events():
            sequence = 0
            item_id = "msg_" + prepared.request_id.removeprefix("ygm_")
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
                    "metadata": safe_metadata,
                },
            }
            yield "data: " + json.dumps(created_event, separators=(",", ":")) + "\n\n"
            text_parts: list[str] = []
            tool_calls: dict[int, dict[str, Any]] = {}
            text_item_added = False
            async for chunk in runtime.gateway.stream(prepared):
                if should_store and await response_cancel_requested(response_id):
                    await fail_response_job(
                        response_id,
                        status="cancelled",
                        code="response_cancelled",
                        message="response execution was cancelled",
                    )
                    sequence += 1
                    cancelled = {
                        "type": "response.cancelled",
                        "sequence_number": sequence,
                        "response": {"id": response_id, "status": "cancelled"},
                    }
                    await append_response_event(response_id, sequence, cancelled)
                    yield "data: " + json.dumps(cancelled, separators=(",", ":")) + "\n\n"
                    yield "data: [DONE]\n\n"
                    return
                if chunk["type"] == "text":
                    if not text_item_added:
                        sequence += 1
                        added = {
                            "type": "response.output_item.added",
                            "sequence_number": sequence,
                            "output_index": 0,
                            "item": {
                                "id": item_id,
                                "type": "message",
                                "status": "in_progress",
                                "role": "assistant",
                                "content": [],
                            },
                        }
                        if should_store:
                            await append_response_event(response_id, sequence, added)
                        yield "data: " + json.dumps(added, separators=(",", ":")) + "\n\n"
                        text_item_added = True
                    text_parts.append(chunk["content"])
                    sequence += 1
                    event = {
                        "type": "response.output_text.delta",
                        "sequence_number": sequence,
                        "item_id": item_id,
                        "output_index": 0,
                        "content_index": 0,
                        "delta": chunk["content"],
                    }
                    if should_store:
                        await append_response_event(response_id, sequence, event)
                    yield "data: " + json.dumps(event, separators=(",", ":")) + "\n\n"
                elif (
                    chunk["type"] == "tool_call"
                    and chunk.get("meta", {}).get("kind") == "caller_function"
                ):
                    meta = chunk["meta"]
                    index = int(meta.get("index") or 0)
                    call = tool_calls.get(index)
                    if call is None:
                        call = {
                            "id": f"fc_{prepared.request_id.removeprefix('ygm_')}_{index}",
                            "type": "function_call",
                            "status": "in_progress",
                            "call_id": str(meta.get("id") or f"call_{index}"),
                            "name": str(meta.get("name") or ""),
                            "arguments": "",
                        }
                        tool_calls[index] = call
                        sequence += 1
                        added = {
                            "type": "response.output_item.added",
                            "sequence_number": sequence,
                            "output_index": index,
                            "item": dict(call),
                        }
                        if should_store:
                            await append_response_event(response_id, sequence, added)
                        yield "data: " + json.dumps(added, separators=(",", ":")) + "\n\n"
                    if meta.get("id"):
                        call["call_id"] = str(meta["id"])
                    if meta.get("name"):
                        call["name"] += str(meta["name"])
                    arguments = str(meta.get("arguments") or "")
                    call["arguments"] += arguments
                    if arguments:
                        sequence += 1
                        delta = {
                            "type": "response.function_call_arguments.delta",
                            "sequence_number": sequence,
                            "item_id": call["id"],
                            "output_index": index,
                            "delta": arguments,
                        }
                        if should_store:
                            await append_response_event(response_id, sequence, delta)
                        yield "data: " + json.dumps(delta, separators=(",", ":")) + "\n\n"
                elif chunk["type"] == "error":
                    sequence += 1
                    error_event = {
                        "type": "error",
                        "sequence_number": sequence,
                        "error": {"message": chunk["content"], "type": "api_error"},
                    }
                    if should_store:
                        await append_response_event(response_id, sequence, error_event)
                    yield ("data: " + json.dumps(error_event, separators=(",", ":")) + "\n\n")
            if text_item_added:
                sequence += 1
                text_done = {
                    "type": "response.output_text.done",
                    "sequence_number": sequence,
                    "item_id": item_id,
                    "output_index": 0,
                    "content_index": 0,
                    "text": "".join(text_parts),
                }
                if should_store:
                    await append_response_event(response_id, sequence, text_done)
                yield ("data: " + json.dumps(text_done, separators=(",", ":")) + "\n\n")
            for index, call in sorted(tool_calls.items()):
                sequence += 1
                arguments_done = {
                    "type": "response.function_call_arguments.done",
                    "sequence_number": sequence,
                    "item_id": call["id"],
                    "output_index": index,
                    "arguments": call["arguments"],
                }
                if should_store:
                    await append_response_event(response_id, sequence, arguments_done)
                yield ("data: " + json.dumps(arguments_done, separators=(",", ":")) + "\n\n")
            sequence += 1
            completed_response = {
                "id": response_id,
                "object": "response",
                "created_at": created,
                "status": "completed",
                "model": prepared.decision.backend.name,
                "output": [
                    *(
                        _response_output(
                            response_id=response_id,
                            text="".join(text_parts),
                            tool_calls=[],
                        )
                        if text_item_added
                        else []
                    ),
                    *(dict(call, status="completed") for call in tool_calls.values()),
                ],
                "metadata": safe_metadata,
                "previous_response_id": body.previous_response_id,
                "conversation": body.conversation,
            }
            completed_event = {
                "type": "response.completed",
                "sequence_number": sequence,
                "response": completed_response,
            }
            if should_store:
                await complete_response_job(response_id, completed_response)
                await append_response_event(response_id, sequence, completed_event)
            yield ("data: " + json.dumps(completed_event, separators=(",", ":")) + "\n\n")
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            events(), media_type="text/event-stream", headers=_headers(prepared)
        )

    try:
        result = await runtime.gateway.execute(prepared)
    except GatewayError as exc:
        if should_store:
            await fail_response_job(
                response_id,
                status="failed",
                code=exc.code,
                message="response execution failed",
            )
        return _openai_error(exc)
    response_object = _response_object(
        response_id=response_id,
        created=created,
        result=result,
        metadata=safe_metadata,
        tools=body.tools,
        tool_choice=body.tool_choice,
        parallel_tool_calls=body.parallel_tool_calls,
    )
    response_object["previous_response_id"] = body.previous_response_id
    response_object["conversation"] = body.conversation
    if should_store:
        await complete_response_job(response_id, response_object)
        await append_response_event(
            response_id,
            1,
            {
                "type": "response.completed",
                "sequence_number": 1,
                "response": response_object,
            },
        )
    return JSONResponse(
        headers=_headers(prepared),
        content=response_object,
    )


@router.get("/responses/{response_id}")
async def retrieve_response(
    response_id: str,
    request: Request,
    principal: Principal = Depends(_gateway_read),
):
    try:
        return await get_response_job(response_id, principal.project_id)
    except ResponseNotFoundError:
        return _openai_error(
            GatewayError(
                "response not found",
                code="response_not_found",
                status_code=404,
                param="response_id",
            )
        )


@router.post("/responses/{response_id}/cancel")
async def cancel_response(
    response_id: str,
    request: Request,
    principal: Principal = Depends(_gateway_invoke),
):
    if not await request_response_cancel(response_id, principal.project_id):
        try:
            existing = await get_response_job(response_id, principal.project_id)
        except ResponseNotFoundError:
            return _openai_error(
                GatewayError(
                    "response not found",
                    code="response_not_found",
                    status_code=404,
                    param="response_id",
                )
            )
        return _openai_error(
            GatewayError(
                f"response is already {existing['status']}",
                code="response_not_cancellable",
                status_code=409,
                param="response_id",
            )
        )
    task = _response_tasks.get(response_id)
    if task is not None:
        task.cancel()
    return {
        "id": response_id,
        "object": "response",
        "status": "cancelling",
    }


@router.get("/responses/{response_id}/events")
async def retrieve_response_events(
    response_id: str,
    request: Request,
    after: int = Query(default=-1, ge=-1),
    stream: bool = Query(default=False),
    principal: Principal = Depends(_gateway_read),
):
    try:
        events = await list_response_events(response_id, principal.project_id, after=after)
    except ResponseNotFoundError:
        return _openai_error(
            GatewayError(
                "response not found",
                code="response_not_found",
                status_code=404,
                param="response_id",
            )
        )
    if not stream:
        return {"object": "list", "data": events}

    async def replay():
        for item in events:
            yield "data: " + json.dumps(item["event"], separators=(",", ":")) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(replay(), media_type="text/event-stream")


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
    except TransformationError:
        log.warning("privacy transformation failed", exc_info=True)
        return _openai_error(
            GatewayError(
                "privacy transformation could not be completed",
                code="transformation_failed",
                status_code=422,
            )
        )
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
    except TransformationError:
        log.info("privacy rehydration failed", exc_info=True)
        return _openai_error(
            GatewayError(
                "tokenization session was unavailable or invalid",
                code="rehydration_failed",
                status_code=404,
            )
        )
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


@router.get("/audit/outbox")
async def audit_outbox_status(
    request: Request,
    _principal: Principal = Depends(_audit_read),
) -> dict:
    """Return content-free durable-delivery health."""
    return await request.app.state.runtime.audit.outbox_status()


@router.post("/audit/outbox/replay")
async def replay_audit_dead_letters(
    request: Request,
    principal: Principal = Depends(_audit_manage),
) -> dict:
    replayed = await request.app.state.runtime.audit.replay_dead_letters(
        project_id=principal.project_id
    )
    return {"replayed": replayed}


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
        subject_id=body.subject_id,
        schema_hash=body.schema_hash,
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
            "subject_id": grant.subject_id,
            "schema_hash": grant.schema_hash,
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
        "subject_id": grant.subject_id,
        "schema_hash": grant.schema_hash,
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
