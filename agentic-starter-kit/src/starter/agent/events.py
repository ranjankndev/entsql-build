"""Streaming events.

The hard constraint: **output guardrails must see the whole answer before the
user does.** Anything streamed token-by-token has already left the building by
the time a guard could block it.

So the kit streams *progress* by default — stage changes, tool calls, guardrail
verdicts — and sends the answer once, after `guard_output` has passed on it.
Set `AGENT_STREAM_TOKENS=true` to also emit draft tokens; then a client MUST
treat `token` events as provisional and replace them with the text of the
final `answer` event, which may be redacted or refused.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

EventType = Literal["status", "token", "tool", "guardrail", "answer", "error", "done"]


@dataclass
class AgentEvent:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        """Server-sent-events frame. `event:` lets clients dispatch by type."""
        return f"event: {self.type}\ndata: {json.dumps(self.data, default=str)}\n\n"

    @classmethod
    def status(cls, stage: str, **data: Any) -> AgentEvent:
        return cls("status", {"stage": stage, **data})

    @classmethod
    def token(cls, text: str) -> AgentEvent:
        return cls("token", {"text": text, "provisional": True})

    @classmethod
    def tool(cls, name: str, phase: str, **data: Any) -> AgentEvent:
        return cls("tool", {"name": name, "phase": phase, **data})

    @classmethod
    def guardrail(cls, stage: str, guard: str, message: str, blocked: bool) -> AgentEvent:
        return cls(
            "guardrail",
            {"stage": stage, "guard": guard, "message": message, "blocked": blocked},
        )

    @classmethod
    def answer(cls, text: str, **data: Any) -> AgentEvent:
        return cls("answer", {"text": text, **data})

    @classmethod
    def error(cls, message: str) -> AgentEvent:
        return cls("error", {"message": message})

    @classmethod
    def done(cls, state: dict[str, Any]) -> AgentEvent:
        return cls(
            "done",
            {
                "thread_id": state.get("thread_id"),
                "trace_id": state.get("trace_id"),
                "stop_reason": state.get("stop_reason"),
                "iterations": state.get("iteration"),
                "tool_calls": state.get("tool_calls_made"),
                "blocked": bool(state.get("blocked")),
                "citations": state.get("citations", []),
            },
        )
