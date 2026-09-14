"""The agent's state object.

A plain `TypedDict` so it works as a LangGraph state and as a dict everywhere
else. Every field is something you will want to see in a trace.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # identity
    thread_id: str
    user_id: str | None
    trace_id: str
    started_at: float

    # conversation
    query: str
    messages: list[dict[str, Any]]
    answer: str

    # loop control
    iteration: int
    tool_calls_made: int
    done: bool
    stop_reason: str  # answered | max_iterations | timeout | blocked | error

    # evidence and side effects
    pending_tool_calls: list[dict[str, Any]]
    scratchpad: list[str]
    tool_results: list[dict[str, Any]]
    citations: list[str]

    # governance
    guardrail_events: list[dict[str, Any]]
    blocked: bool
    pending_approval: dict[str, Any] | None
    approved_tools: list[str]
    error: str | None
    usage: dict[str, int]
    events_emitted: int


def new_state(query: str, thread_id: str | None = None, user_id: str | None = None) -> AgentState:
    return AgentState(
        thread_id=thread_id or uuid.uuid4().hex,
        user_id=user_id,
        trace_id=uuid.uuid4().hex,
        started_at=time.time(),
        query=query,
        messages=[],
        answer="",
        iteration=0,
        tool_calls_made=0,
        done=False,
        stop_reason="",
        pending_tool_calls=[],
        scratchpad=[],
        tool_results=[],
        citations=[],
        guardrail_events=[],
        blocked=False,
        pending_approval=None,
        approved_tools=[],
        error=None,
        usage={},
        events_emitted=0,
    )
