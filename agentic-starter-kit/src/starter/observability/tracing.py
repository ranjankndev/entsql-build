"""Tracing seam.

One interface, three implementations: `none` (structured logs only), `langfuse`
(hosted or self-hosted), and whatever you add. Nothing in the agent, guardrails
or memory imports a vendor SDK — they all take a `Tracer`.

Spans carry the numbers you will actually be asked about in review: latency,
token usage, tool outcome, guardrail verdict, and the eval score attached later
to the same trace id.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from starter.settings import Settings, get_settings

log = logging.getLogger("starter.trace")


@dataclass
class Span:
    name: str
    kind: str = "span"  # span | generation | tool | guard
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    input: Any = None
    output: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    started: float = field(default_factory=time.perf_counter)
    duration_ms: float = 0.0
    error: str | None = None

    def end(self, output: Any = None, error: str | None = None, **metadata: Any) -> Span:
        self.output = output if output is not None else self.output
        self.error = error
        self.metadata.update(metadata)
        self.duration_ms = (time.perf_counter() - self.started) * 1000
        return self


@runtime_checkable
class Tracer(Protocol):
    provider: str

    @contextmanager
    def span(self, name: str, kind: str = "span", **fields: Any) -> Iterator[Span]: ...

    def score(self, trace_id: str, name: str, value: float, comment: str = "") -> None: ...

    def event(self, name: str, **fields: Any) -> None: ...

    def flush(self) -> None: ...


class NoOpTracer:
    """Default. Emits one structured JSON log line per span — grep-able,
    ingestible by Azure Monitor, and free."""

    provider = "none"

    def __init__(self, trace_id: str | None = None) -> None:
        self.trace_id = trace_id or uuid.uuid4().hex

    @contextmanager
    def span(self, name: str, kind: str = "span", **fields: Any) -> Iterator[Span]:
        span = Span(name=name, kind=kind, trace_id=self.trace_id, **_split(fields))
        try:
            yield span
        except Exception as exc:
            span.end(error=f"{type(exc).__name__}: {exc}")
            self._emit(span)
            raise
        else:
            if span.duration_ms == 0.0:
                span.end(span.output)
            self._emit(span)

    def _emit(self, span: Span) -> None:
        log.info(
            json.dumps(
                {
                    "trace_id": span.trace_id,
                    "span_id": span.span_id,
                    "name": span.name,
                    "kind": span.kind,
                    "duration_ms": round(span.duration_ms, 2),
                    "error": span.error,
                    "metadata": span.metadata,
                },
                default=str,
            )
        )

    def score(self, trace_id: str, name: str, value: float, comment: str = "") -> None:
        log.info(json.dumps({"trace_id": trace_id, "score": name, "value": value, "comment": comment}))

    def event(self, name: str, **fields: Any) -> None:
        log.info(json.dumps({"event": name, **fields}, default=str))

    def flush(self) -> None:
        return None


class LangfuseTracer(NoOpTracer):
    """Langfuse-backed tracer. Falls back to structured logs if the SDK or the
    credentials are missing, so a broken observability config never takes the
    service down."""

    provider = "langfuse"

    def __init__(self, settings: Settings | None = None, trace_id: str | None = None) -> None:
        super().__init__(trace_id)
        s = settings or get_settings()
        self._client = None
        self._trace = None
        try:
            from langfuse import Langfuse  # type: ignore[import-not-found]

            self._client = Langfuse(
                public_key=s.langfuse_public_key,
                secret_key=s.langfuse_secret_key,
                host=s.langfuse_host,
            )
        except Exception as exc:
            log.warning("langfuse unavailable, falling back to log tracer: %s", exc)

    def start_trace(self, name: str, user_id: str | None = None, **metadata: Any) -> str:
        if self._client is not None:
            self._trace = self._client.trace(
                id=self.trace_id, name=name, user_id=user_id, metadata=metadata
            )
        return self.trace_id

    @contextmanager
    def span(self, name: str, kind: str = "span", **fields: Any) -> Iterator[Span]:
        span = Span(name=name, kind=kind, trace_id=self.trace_id, **_split(fields))
        handle = None
        if self._trace is not None:
            factory = self._trace.generation if kind == "generation" else self._trace.span
            handle = factory(name=name, input=span.input, metadata=span.metadata)
        try:
            yield span
        except Exception as exc:
            span.end(error=f"{type(exc).__name__}: {exc}")
            self._close(handle, span)
            self._emit(span)
            raise
        else:
            if span.duration_ms == 0.0:
                span.end(span.output)
            self._close(handle, span)
            self._emit(span)

    @staticmethod
    def _close(handle: Any, span: Span) -> None:
        if handle is None:
            return
        try:
            handle.end(output=span.output, metadata={**span.metadata, "error": span.error})
        except Exception as exc:
            log.warning("langfuse span end failed: %s", exc)

    def score(self, trace_id: str, name: str, value: float, comment: str = "") -> None:
        super().score(trace_id, name, value, comment)
        if self._client is not None:
            try:
                self._client.score(trace_id=trace_id, name=name, value=value, comment=comment)
            except Exception as exc:
                log.warning("langfuse score failed: %s", exc)

    def flush(self) -> None:
        if self._client is not None:
            try:
                self._client.flush()
            except Exception as exc:
                log.warning("langfuse flush failed: %s", exc)


def _split(fields: dict[str, Any]) -> dict[str, Any]:
    """Accept `input=`/`output=` as span fields, everything else as metadata."""
    known = {k: fields.pop(k) for k in ("input", "output") if k in fields}
    if fields:
        known["metadata"] = fields
    return known


def get_tracer(settings: Settings | None = None, trace_id: str | None = None) -> Tracer:
    s = settings or get_settings()
    if s.observability_provider == "langfuse":
        return LangfuseTracer(s, trace_id)
    if s.observability_provider == "otel":
        from starter.observability.otel import OTelTracer

        return OTelTracer(s, trace_id)
    return NoOpTracer(trace_id)


@contextmanager
def trace_run(name: str, tracer: Tracer | None = None, **metadata: Any) -> Iterator[Span]:
    """Top-level span for one agent run."""
    t = tracer or get_tracer()
    if isinstance(t, LangfuseTracer):
        t.start_trace(name, **metadata)
    with t.span(name, kind="span", **metadata) as span:
        yield span
    t.flush()
