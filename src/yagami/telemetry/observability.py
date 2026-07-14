from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter
from typing import Iterator

from opentelemetry import metrics, trace
from opentelemetry.trace import Span, SpanKind
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest


class GatewayMetrics:
    """Low-cardinality gateway metrics; prompts and project IDs are never labels."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.requests = Counter(
            "yagami_gateway_requests_total",
            "Gateway requests by backend, sensitivity, and outcome.",
            ("backend", "is_local", "sensitivity", "outcome"),
            registry=self.registry,
        )
        self.duration = Histogram(
            "yagami_gateway_request_duration_seconds",
            "End-to-end gateway request duration.",
            ("backend", "is_local"),
            registry=self.registry,
        )
        self.tokens = Counter(
            "yagami_gateway_tokens_total",
            "Estimated gateway tokens by direction.",
            ("backend", "direction"),
            registry=self.registry,
        )
        self.policy_denials = Counter(
            "yagami_policy_denials_total",
            "Requests denied by the policy engine.",
            ("sensitivity",),
            registry=self.registry,
        )
        self.policy_matches = Counter(
            "yagami_policy_rule_matches_total",
            "Policy rule matches. Rule IDs are administrator-controlled.",
            ("rule_id",),
            registry=self.registry,
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)


_tracer = trace.get_tracer("yagami.gateway")
_meter = metrics.get_meter("yagami.gateway")
_genai_duration = _meter.create_histogram(
    "gen_ai.client.operation.duration",
    unit="s",
    description="GenAI gateway operation duration.",
)
_genai_tokens = _meter.create_histogram(
    "gen_ai.client.token.usage",
    unit="{token}",
    description="Estimated GenAI gateway input and output token usage.",
)

_PROVIDERS = {
    "anthropic": "anthropic",
    "gemini": "gcp.gemini",
    "groq": "groq",
    "mistral": "mistral_ai",
    "openai": "openai",
}


def genai_provider(backend: str, *, is_local: bool) -> str:
    if is_local:
        return "local"
    return _PROVIDERS.get(backend, backend)


@contextmanager
def gateway_span(
    *,
    request_id: str,
    backend: str,
    is_local: bool,
    project_id: str,
    sensitivity: str,
    policy_hash: str,
    temperature: float,
    max_tokens: int,
    conversation_id: str | None,
) -> Iterator[Span]:
    """Emit GenAI-compatible metadata without recording prompts or responses."""
    provider = genai_provider(backend, is_local=is_local)
    with _tracer.start_as_current_span(
        f"chat {backend}", kind=SpanKind.INTERNAL if is_local else SpanKind.CLIENT
    ) as span:
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.request.model", backend)
        span.set_attribute("gen_ai.provider.name", provider)
        span.set_attribute("gen_ai.request.temperature", temperature)
        span.set_attribute("gen_ai.request.max_tokens", max_tokens)
        span.set_attribute("gen_ai.output.type", "text")
        if conversation_id:
            span.set_attribute("gen_ai.conversation.id", conversation_id)
        span.set_attribute("yagami.request.id", request_id)
        span.set_attribute("yagami.project.id", project_id)
        span.set_attribute("yagami.backend.is_local", is_local)
        span.set_attribute("yagami.sensitivity", sensitivity)
        span.set_attribute("yagami.policy.hash", policy_hash)
        yield span


def record_genai_metrics(
    *,
    backend: str,
    is_local: bool,
    duration_seconds: float,
    input_tokens: int,
    output_tokens: int,
) -> None:
    attributes = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": genai_provider(backend, is_local=is_local),
        "gen_ai.request.model": backend,
    }
    _genai_duration.record(duration_seconds, attributes)
    _genai_tokens.record(input_tokens, {**attributes, "gen_ai.token.type": "input"})
    _genai_tokens.record(output_tokens, {**attributes, "gen_ai.token.type": "output"})


@contextmanager
def timed_histogram(histogram, *labels: str) -> Iterator[None]:
    started = perf_counter()
    try:
        yield
    finally:
        histogram.labels(*labels).observe(perf_counter() - started)
