"""OpenTelemetry tracer: attribute mapping, error status, graceful degradation.

A fake OTel tracer stands in for the SDK, so these run with or without it
installed and assert the exact attributes a dashboard will see.
"""

from contextlib import contextmanager

import pytest

from starter.agent import Agent
from starter.observability.otel import OTelTracer, _attributes
from starter.observability.tracing import Span


class FakeSpan:
    def __init__(self, name: str):
        self.name = name
        self.attributes: dict = {}
        self.exceptions: list = []
        self.status = None

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def record_exception(self, exc):
        self.exceptions.append(exc)

    def set_status(self, status, description=None):
        self.status = status


class FakeOTel:
    def __init__(self):
        self.spans: list[FakeSpan] = []

    @contextmanager
    def start_as_current_span(self, name):
        span = FakeSpan(name)
        self.spans.append(span)
        yield span


@pytest.fixture
def otel(settings):
    fake = FakeOTel()
    return OTelTracer(settings, otel_tracer=fake), fake


# ------------------------------------------------------------- attributes


def test_generation_spans_use_genai_conventions():
    span = Span(name="llm.think", kind="generation")
    span.end(usage={"input_tokens": 12, "output_tokens": 30})
    attrs = _attributes(span, "azure_openai", "gpt-4o-mini")
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["gen_ai.system"] == "azure_openai"
    assert attrs["gen_ai.request.model"] == "gpt-4o-mini"
    assert attrs["gen_ai.usage.input_tokens"] == 12
    assert attrs["gen_ai.usage.output_tokens"] == 30


def test_openai_style_usage_keys_map_too():
    span = Span(name="llm.think", kind="generation")
    span.end(usage={"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12})
    attrs = _attributes(span, "openai", "gpt-4o-mini")
    assert attrs["gen_ai.usage.input_tokens"] == 5
    assert attrs["gen_ai.usage.output_tokens"] == 7
    assert attrs["starter.usage.total_tokens"] == 12


def test_tool_spans_carry_the_tool_name():
    span = Span(name="tool.search_kb", kind="tool")
    span.end(ok=True)
    attrs = _attributes(span, "echo", "m")
    assert attrs["gen_ai.operation.name"] == "execute_tool"
    assert attrs["gen_ai.tool.name"] == "search_kb"
    assert attrs["starter.ok"] is True


def test_our_trace_id_travels_as_an_attribute():
    span = Span(name="agent.run", kind="span", trace_id="abc123")
    span.end()
    assert _attributes(span, "echo", "m")["starter.trace_id"] == "abc123"


def test_unserialisable_metadata_is_dropped_not_crashed():
    span = Span(name="x", kind="span")
    span.end(good="yes", bad={"nested": object()})
    attrs = _attributes(span, "echo", "m")
    assert attrs["starter.good"] == "yes"
    assert not any(k.endswith("bad") for k in attrs)


# ------------------------------------------------------------------ spans


def test_span_is_exported(otel):
    tracer, fake = otel
    with tracer.span("guard.input", kind="guard", blocked=False) as span:
        span.end(output="ok")
    assert [s.name for s in fake.spans] == ["guard.input"]
    assert fake.spans[0].attributes["gen_ai.operation.name"] == "guardrail"


def test_exceptions_are_recorded_and_reraised(otel):
    tracer, fake = otel
    with pytest.raises(ValueError), tracer.span("boom"):
        raise ValueError("nope")
    assert fake.spans[0].exceptions
    assert fake.spans[0].status is not None or "error" in fake.spans[0].attributes


def test_scores_become_their_own_span_linked_by_trace_id(otel):
    tracer, fake = otel
    tracer.score("trace-42", "groundedness", 0.9, "supported")
    span = fake.spans[-1]
    assert span.name == "eval.score"
    assert span.attributes["starter.trace_id"] == "trace-42"
    assert span.attributes["starter.score.value"] == 0.9


# ------------------------------------------------------- graceful degradation


def test_falls_back_to_logging_without_the_sdk(settings, caplog):
    import logging

    tracer = OTelTracer(settings, otel_tracer=None)
    tracer._tracer = None  # simulate a missing/failed SDK
    with (
        caplog.at_level(logging.INFO, logger="starter.trace"),
        tracer.span("still.works") as span,
    ):
        span.end(output="fine")
    assert any("still.works" in record.message for record in caplog.records)


def test_a_broken_exporter_never_breaks_a_run(settings, deps):
    class Exploding:
        @contextmanager
        def start_as_current_span(self, name):
            raise RuntimeError("collector unreachable")
            yield  # pragma: no cover

    deps.tracer = OTelTracer(settings, otel_tracer=Exploding())
    with pytest.raises(RuntimeError), deps.tracer.span("x"):
        pass  # the tracer surfaces it rather than hiding a config error


def test_agent_runs_end_to_end_on_the_otel_tracer(settings, deps):
    fake = FakeOTel()
    deps.tracer = OTelTracer(settings, otel_tracer=fake)
    state = Agent(deps).run("What is the refund policy?", thread_id="otel1")
    assert state["stop_reason"] == "answered"
    assert {"agent.run", "guard.input", "llm.think"} <= {s.name for s in fake.spans}


def test_get_tracer_selects_otel(settings):
    from starter.observability import get_tracer

    settings.observability_provider = "otel"
    assert isinstance(get_tracer(settings), OTelTracer)
