from __future__ import annotations

from contextlib import contextmanager

from opentelemetry.trace import SpanKind

from yagami.telemetry import observability


class FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}

    def set_attribute(self, name, value):
        self.attributes[name] = value


class FakeTracer:
    def __init__(self) -> None:
        self.name = ""
        self.kind = None
        self.span = FakeSpan()

    @contextmanager
    def start_as_current_span(self, name, *, kind):
        self.name = name
        self.kind = kind
        yield self.span


class FakeHistogram:
    def __init__(self) -> None:
        self.records: list[tuple[float, dict]] = []

    def record(self, value, attributes):
        self.records.append((value, attributes))


def test_gateway_span_uses_content_free_genai_semantic_conventions(monkeypatch):
    tracer = FakeTracer()
    monkeypatch.setattr(observability, "_tracer", tracer)

    with observability.gateway_span(
        request_id="ygm_test",
        backend="anthropic",
        is_local=False,
        project_id="alpha",
        sensitivity="secret",
        policy_hash="sha256:abc",
        temperature=0.2,
        max_tokens=1024,
        conversation_id="conversation-one",
    ):
        pass

    assert tracer.name == "chat anthropic"
    assert tracer.kind == SpanKind.CLIENT
    assert tracer.span.attributes["gen_ai.provider.name"] == "anthropic"
    assert tracer.span.attributes["gen_ai.operation.name"] == "chat"
    assert tracer.span.attributes["gen_ai.request.temperature"] == 0.2
    assert tracer.span.attributes["gen_ai.conversation.id"] == "conversation-one"
    assert "prompt" not in " ".join(tracer.span.attributes)


def test_local_model_span_uses_internal_kind(monkeypatch):
    tracer = FakeTracer()
    monkeypatch.setattr(observability, "_tracer", tracer)

    with observability.gateway_span(
        request_id="ygm_test",
        backend="ollama",
        is_local=True,
        project_id="local",
        sensitivity="none",
        policy_hash="sha256:abc",
        temperature=0.7,
        max_tokens=2048,
        conversation_id=None,
    ):
        pass

    assert tracer.kind == SpanKind.INTERNAL
    assert tracer.span.attributes["gen_ai.provider.name"] == "local"
    assert "gen_ai.conversation.id" not in tracer.span.attributes


def test_genai_metrics_use_standard_names_and_low_cardinality_labels(monkeypatch):
    duration = FakeHistogram()
    tokens = FakeHistogram()
    monkeypatch.setattr(observability, "_genai_duration", duration)
    monkeypatch.setattr(observability, "_genai_tokens", tokens)

    observability.record_genai_metrics(
        backend="gemini",
        is_local=False,
        duration_seconds=1.25,
        input_tokens=10,
        output_tokens=4,
    )

    assert duration.records[0][0] == 1.25
    assert duration.records[0][1]["gen_ai.provider.name"] == "gcp.gemini"
    assert [record[1]["gen_ai.token.type"] for record in tokens.records] == [
        "input",
        "output",
    ]
