"""FastAPI surface: one chat endpoint, health probes, and a guardrail probe.

Kept thin on purpose — all behaviour lives in `starter.agent`. The probes are
what Azure Container Apps uses for liveness/readiness.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from starter import __version__
from starter.agent import build_agent
from starter.guardrails import GuardContext, Stage, build_pipeline
from starter.settings import get_settings

app = FastAPI(title="Agentic starter kit", version=__version__)

_agent = None


def agent() -> Any:
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=16_000)
    thread_id: str | None = None
    user_id: str | None = None


class ChatResponse(BaseModel):
    answer: str
    thread_id: str
    trace_id: str
    stop_reason: str
    blocked: bool
    iterations: int
    tool_calls: int
    citations: list[str]


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/readyz")
def readyz() -> dict[str, Any]:
    s = get_settings()
    return {
        "status": "ready",
        "llm_provider": s.llm_provider,
        "memory_backend": s.memory_backend,
        "observability": s.observability_provider,
        "tools": agent().deps.tools.names(),
    }


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    try:
        state = agent().run(request.query, request.thread_id, request.user_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"agent failure: {type(exc).__name__}") from exc
    return ChatResponse(
        answer=state.get("answer", ""),
        thread_id=state["thread_id"],
        trace_id=state.get("trace_id", ""),
        stop_reason=state.get("stop_reason", ""),
        blocked=bool(state.get("blocked")),
        iterations=state.get("iteration", 0),
        tool_calls=state.get("tool_calls_made", 0),
        citations=state.get("citations", []),
    )


@app.post("/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    """Server-sent events: `status`, `tool`, `guardrail`, `token`, `answer`, `done`.

    `token` events are provisional drafts and are only emitted when
    `AGENT_STREAM_TOKENS=true`; clients must render the final `answer` event's
    text, which is what the output guardrails actually approved.
    """

    def frames() -> Any:
        for event in agent().stream(request.query, request.thread_id, request.user_id):
            yield event.to_sse()

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class GuardRequest(BaseModel):
    text: str
    stage: Stage = Stage.INPUT


@app.post("/guardrails/check")
def guardrails_check(request: GuardRequest) -> dict[str, Any]:
    outcome = build_pipeline().run_safe(request.text, GuardContext(stage=request.stage))
    return {
        "blocked": outcome.blocked,
        "text": outcome.text,
        "violations": [
            {"guard": r.guard, "severity": r.severity.value, "message": r.message}
            for r in outcome.violations
        ],
    }
