from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter
from typing import Iterator

from opentelemetry import trace
from opentelemetry.trace import Span
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


@contextmanager
def gateway_span(
    *,
    request_id: str,
    backend: str,
    is_local: bool,
    project_id: str,
    sensitivity: str,
    policy_hash: str,
) -> Iterator[Span]:
    """Emit GenAI-compatible metadata without recording prompts or responses."""
    with _tracer.start_as_current_span(f"chat {backend}") as span:
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.request.model", backend)
        span.set_attribute("gen_ai.provider.name", "local" if is_local else backend)
        span.set_attribute("yagami.request.id", request_id)
        span.set_attribute("yagami.project.id", project_id)
        span.set_attribute("yagami.backend.is_local", is_local)
        span.set_attribute("yagami.sensitivity", sensitivity)
        span.set_attribute("yagami.policy.hash", policy_hash)
        yield span


@contextmanager
def timed_histogram(histogram, *labels: str) -> Iterator[None]:
    started = perf_counter()
    try:
        yield
    finally:
        histogram.labels(*labels).observe(perf_counter() - started)
