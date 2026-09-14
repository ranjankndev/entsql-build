import json
import logging

from starter.observability import NoOpTracer, trace_run


def test_span_emits_structured_log(caplog):
    tracer = NoOpTracer()
    with (
        caplog.at_level(logging.INFO, logger="starter.trace"),
        tracer.span("unit", kind="tool", foo="bar") as span,
    ):
        span.end(output="done")
    payload = json.loads(caplog.records[-1].message)
    assert payload["name"] == "unit"
    assert payload["kind"] == "tool"
    assert payload["metadata"]["foo"] == "bar"


def test_span_records_errors(caplog):
    tracer = NoOpTracer()
    with caplog.at_level(logging.INFO, logger="starter.trace"):
        try:
            with tracer.span("boom"):
                raise ValueError("nope")
        except ValueError:
            pass
    assert "ValueError" in json.loads(caplog.records[-1].message)["error"]


def test_trace_run_shares_one_trace_id():
    tracer = NoOpTracer()
    with trace_run("run", tracer=tracer) as span:
        assert span.trace_id == tracer.trace_id
