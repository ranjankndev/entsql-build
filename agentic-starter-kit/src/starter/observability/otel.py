"""OpenTelemetry tracer.

Why bother when Langfuse already works: OTel is what the *rest* of your
platform speaks. Exporting there puts agent spans in the same waterfall as the
HTTP request, the database call and the queue consumer — so "the agent is slow"
gets answered with the actual span that was slow, not with two tools open side
by side. Langfuse gives you prompt-level detail; OTel gives you system context.
Running both is normal.

Attribute naming follows the OpenTelemetry **GenAI semantic conventions**
(`gen_ai.*`), so vendor dashboards understand the spans without custom parsing.
Those conventions are still evolving upstream; the mapping lives in one
function (`_attributes`) precisely so tracking a change is a small diff.

The OTel SDK is an optional dependency (`pip install '.[otel]'`). Without it —
or if the exporter cannot start — this degrades to the structured-log tracer,
because observability must never be the thing that takes the service down.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from starter.observability.tracing import NoOpTracer, Span
from starter.settings import Settings, get_settings

log = logging.getLogger("starter.trace")

# Our span kinds -> the GenAI operation names OTel dashboards group by.
OPERATION_NAMES: dict[str, str] = {
    "generation": "chat",
    "tool": "execute_tool",
    "guard": "guardrail",
    "span": "workflow",
}


def _attributes(span: Span, provider: str, model: str) -> dict[str, Any]:
    """Map one `Span` onto GenAI semantic-convention attributes.

    Kept as one function so an upstream convention change is a one-place edit.
    """
    attributes: dict[str, Any] = {
        "gen_ai.operation.name": OPERATION_NAMES.get(span.kind, "workflow"),
        "starter.span.kind": span.kind,
        "starter.trace_id": span.trace_id,
    }
    if span.kind == "generation":
        attributes["gen_ai.system"] = provider
        if model:
            attributes["gen_ai.request.model"] = model
    if span.kind == "tool":
        attributes["gen_ai.tool.name"] = span.name.removeprefix("tool.")

    metadata = span.metadata or {}
    usage = metadata.get("usage") or {}
    for source, target in (
        ("input_tokens", "gen_ai.usage.input_tokens"),
        ("prompt_tokens", "gen_ai.usage.input_tokens"),
        ("output_tokens", "gen_ai.usage.output_tokens"),
        ("completion_tokens", "gen_ai.usage.output_tokens"),
        ("total_tokens", "starter.usage.total_tokens"),
    ):
        if isinstance(usage.get(source), int):
            attributes[target] = usage[source]

    for key, value in metadata.items():
        if key == "usage":
            continue
        if isinstance(value, (str, int, float, bool)):
            attributes[f"starter.{key}"] = value
        elif isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
            attributes[f"starter.{key}"] = list(value)
    return attributes


class OTelTracer(NoOpTracer):
    """Emits OTel spans *and* the structured log line, so nothing is lost if
    the collector is unreachable."""

    provider = "otel"

    def __init__(
        self,
        settings: Settings | None = None,
        trace_id: str | None = None,
        otel_tracer: Any = None,
    ) -> None:
        super().__init__(trace_id)
        s = settings or get_settings()
        self._model = s.llm_model
        self._llm_provider = s.llm_provider
        self._tracer = otel_tracer
        if self._tracer is None:
            self._tracer = self._build(s)

    @staticmethod
    def _build(s: Settings) -> Any:
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider(
                resource=Resource.create(
                    {"service.name": s.otel_service_name, "deployment.environment": s.app_env}
                )
            )
            if s.otel_exporter_endpoint:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )

                provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(endpoint=s.otel_exporter_endpoint))
                )
            elif s.otel_console:
                from opentelemetry.sdk.trace.export import ConsoleSpanExporter

                provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            trace.set_tracer_provider(provider)
            return trace.get_tracer("starter.agent")
        except Exception as exc:
            log.warning("opentelemetry unavailable, falling back to log tracer: %s", exc)
            return None

    @contextmanager
    def span(self, name: str, kind: str = "span", **fields: Any) -> Iterator[Span]:
        if self._tracer is None:
            with super().span(name, kind, **fields) as span:
                yield span
            return

        with (
            super().span(name, kind, **fields) as span,
            self._tracer.start_as_current_span(name) as otel_span,
        ):
            try:
                yield span
            except Exception as exc:
                self._finish(otel_span, span, error=exc)
                raise
            self._finish(otel_span, span)

    def _finish(self, otel_span: Any, span: Span, error: BaseException | None = None) -> None:
        try:
            if span.duration_ms == 0.0:
                span.end(span.output)
            for key, value in _attributes(span, self._llm_provider, self._model).items():
                otel_span.set_attribute(key, value)
            if error is not None:
                otel_span.record_exception(error)
                self._set_error(otel_span, f"{type(error).__name__}: {error}")
            elif span.error:
                self._set_error(otel_span, span.error)
        except Exception as exc:
            log.warning("otel span export failed: %s", exc)

    @staticmethod
    def _set_error(otel_span: Any, message: str) -> None:
        try:
            from opentelemetry.trace import Status, StatusCode

            otel_span.set_status(Status(StatusCode.ERROR, message))
        except Exception:
            otel_span.set_attribute("error", message)

    def score(self, trace_id: str, name: str, value: float, comment: str = "") -> None:
        """OTel has no "attach a score to a finished trace" primitive, so a
        score becomes its own short span carrying the trace id it refers to.
        Query by `starter.trace_id` to line scores up with their run."""
        super().score(trace_id, name, value, comment)
        if self._tracer is None:
            return
        try:
            with self._tracer.start_as_current_span("eval.score") as otel_span:
                otel_span.set_attribute("starter.trace_id", trace_id)
                otel_span.set_attribute("starter.score.name", name)
                otel_span.set_attribute("starter.score.value", float(value))
                if comment:
                    otel_span.set_attribute("starter.score.comment", comment[:500])
        except Exception as exc:
            log.warning("otel score export failed: %s", exc)

    def flush(self) -> None:
        if self._tracer is None:
            return
        try:
            from opentelemetry import trace

            provider = trace.get_tracer_provider()
            if hasattr(provider, "force_flush"):
                provider.force_flush()
        except Exception as exc:
            log.warning("otel flush failed: %s", exc)
