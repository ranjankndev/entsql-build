"""Agent nodes.

Each node is `(state, deps) -> state-patch`. They are deliberately free of any
graph framework so the same functions drive both `agent/loop.py` (dependency
free) and `agent/graph.py` (LangGraph). That is the whole point of the seam:
you can port to another orchestrator without rewriting behaviour.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from starter.agent.prompts import REFLECTION_PROMPT, system_prompt
from starter.agent.state import AgentState
from starter.guardrails import GuardContext, GuardrailPipeline, Stage
from starter.llm import LLMProvider, LLMResponse, stream_or_complete
from starter.memory import ContextMemory
from starter.observability import Tracer
from starter.settings import Settings
from starter.tools import ToolRegistry


@dataclass
class AgentDeps:
    """Everything a node may touch. Injected, never imported globally."""

    llm: LLMProvider
    tools: ToolRegistry
    memory: ContextMemory
    guardrails: GuardrailPipeline
    tracer: Tracer
    settings: Settings
    agent_name: str = "Assistant"
    domain: str = "general questions"
    reflect: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


def _record_guard(state: AgentState, stage: str, outcome: Any) -> None:
    for result in outcome.results:
        state.setdefault("guardrail_events", []).append(
            {
                "stage": stage,
                "guard": result.guard,
                "passed": result.passed,
                "severity": result.severity.value,
                "message": result.message,
            }
        )


# --------------------------------------------------------------------- nodes


def guard_input(state: AgentState, deps: AgentDeps) -> AgentState:
    ctx = GuardContext(
        stage=Stage.INPUT, thread_id=state["thread_id"], user_id=state.get("user_id")
    )
    with deps.tracer.span("guard.input", kind="guard", input=state["query"]) as span:
        outcome = deps.guardrails.run_safe(state["query"], ctx)
        _record_guard(state, "input", outcome)
        span.end(output=outcome.text, blocked=outcome.blocked)
    if outcome.blocked:
        state.update(
            blocked=True,
            done=True,
            stop_reason="blocked",
            answer=outcome.text,
        )
    else:
        state["query"] = outcome.text
    return state


def load_context(state: AgentState, deps: AgentDeps) -> AgentState:
    with deps.tracer.span("memory.context", kind="span") as span:
        deps.memory.system_prompt = system_prompt(
            deps.agent_name, deps.domain, deps.tools.names()
        )
        messages = deps.memory.build_context(
            state["thread_id"], state["query"], owner_id=state.get("user_id")
        )
        state["messages"] = messages
        span.end(output=f"{len(messages)} messages", message_count=len(messages))
    return state


def think(state: AgentState, deps: AgentDeps) -> AgentState:
    """One model call: either a tool request or a final answer."""
    with deps.tracer.span("llm.think", kind="generation", input=state["messages"][-1]) as span:
        response = deps.llm.complete(state["messages"], tools=deps.tools.schemas())
        span.end(
            output=response.text[:500],
            tool_calls=[c["name"] for c in response.tool_calls],
            usage=response.usage,
        )

    _apply_response(state, response)
    return state


def _apply_response(state: AgentState, response: LLMResponse) -> AgentState:
    """Fold one model response into the state: tool calls, or a final answer."""
    usage = state.setdefault("usage", {})
    for key, value in (response.usage or {}).items():
        if isinstance(value, int):
            usage[key] = usage.get(key, 0) + value

    if response.tool_calls:
        state["messages"].append(
            {"role": "assistant", "content": response.text or "", "tool_calls": response.tool_calls}
        )
        state["pending_approval"] = None
        state.setdefault("scratchpad", []).append(
            "calling: " + ", ".join(c["name"] for c in response.tool_calls)
        )
        state["pending_tool_calls"] = response.tool_calls
    else:
        state["answer"] = response.text
        state["messages"].append({"role": "assistant", "content": response.text})
        state["pending_tool_calls"] = []
        state["done"] = True
        state["stop_reason"] = "answered"
    return state


def think_streaming(state: AgentState, deps: AgentDeps) -> Iterator[str]:
    """`think`, yielding text deltas as the model produces them.

    Falls back to one blocking call when the provider cannot stream, so callers
    never need to ask which provider they have.
    """
    with deps.tracer.span("llm.think", kind="generation", input=state["messages"][-1]) as span:
        response: LLMResponse | None = None
        for chunk in stream_or_complete(deps.llm, state["messages"], deps.tools.schemas()):
            if isinstance(chunk, LLMResponse):
                response = chunk
            else:
                yield chunk
        if response is None:
            response = LLMResponse(text="")
        span.end(
            output=response.text[:500],
            tool_calls=[c["name"] for c in response.tool_calls],
            usage=response.usage,
        )
    _apply_response(state, response)


def act(state: AgentState, deps: AgentDeps) -> AgentState:
    """Execute the requested tool calls, each one guardrailed both ways."""
    calls: list[dict[str, Any]] = state.get("pending_tool_calls", [])
    for call in calls:
        name, args = call["name"], call.get("args", {})
        ctx = GuardContext(
            stage=Stage.TOOL,
            thread_id=state["thread_id"],
            user_id=state.get("user_id"),
            tool_name=name,
        )
        pre = deps.guardrails.run_safe(json.dumps(args, default=str), ctx)
        _record_guard(state, "tool_call", pre)
        if pre.blocked:
            observation = f"tool call blocked by policy: {pre.blocking_result.message}"
            _append_tool_message(state, call, observation, ok=False)
            continue

        tool = deps.tools.tools.get(name)
        if tool is not None and tool.requires_approval and not _approved(state, name):
            state["pending_approval"] = {"tool": name, "args": args}
            state["done"] = True
            state["stop_reason"] = "awaiting_approval"
            _append_tool_message(state, call, "awaiting human approval", ok=False)
            state["pending_tool_calls"] = []
            return state

        with deps.tracer.span(f"tool.{name}", kind="tool", input=args) as span:
            result = deps.tools.invoke(name, args)
            span.end(output=result.content[:500], ok=result.ok, duration_ms=result.duration_ms)

        post = deps.guardrails.run_safe(result.content, ctx)
        _record_guard(state, "tool_result", post)
        observation = post.text if not post.blocked else "tool output withheld by policy"

        state["tool_calls_made"] = state.get("tool_calls_made", 0) + 1
        state.setdefault("tool_results", []).append(
            {"tool": name, "args": args, "ok": result.ok, "content": observation}
        )
        state.setdefault("citations", []).append(f"[tool:{name}]")
        _append_tool_message(state, call, observation, ok=result.ok)
    return state


def _approved(state: AgentState, tool_name: str) -> bool:
    """Placeholder approval check.

    Wire this to your real approval store (a ticket, a Teams card, a LangGraph
    `interrupt_before=["act"]` checkpoint). Until then a `requires_approval`
    tool pauses the run with `stop_reason="awaiting_approval"`, and the caller
    resumes by re-running with the tool name listed in `approved_tools`.
    """
    return tool_name in (state.get("approved_tools") or [])


def _append_tool_message(
    state: AgentState, call: dict[str, Any], content: str, ok: bool
) -> None:
    state["messages"].append(
        {
            "role": "tool",
            "name": call["name"],
            "tool_call_id": call.get("id", ""),
            "content": content if ok else f"[failed] {content}",
        }
    )


def reflect(state: AgentState, deps: AgentDeps) -> AgentState:
    """Self-check before answering. Cheap, optional, measurable in evals."""
    if not deps.reflect or not state.get("answer"):
        return state
    with deps.tracer.span("llm.reflect", kind="generation") as span:
        verdict = deps.llm.complete(
            [
                *state["messages"],
                {"role": "user", "content": REFLECTION_PROMPT},
            ]
        ).text.strip()
        span.end(output=verdict[:200])
    if verdict.upper().startswith("RETRY"):
        state["done"] = False
        state["stop_reason"] = ""
        state.setdefault("scratchpad", []).append(f"reflection: {verdict}")
        state["messages"].append({"role": "user", "content": verdict})
    return state


def guard_output(state: AgentState, deps: AgentDeps) -> AgentState:
    ctx = GuardContext(
        stage=Stage.OUTPUT, thread_id=state["thread_id"], user_id=state.get("user_id")
    )
    with deps.tracer.span("guard.output", kind="guard") as span:
        outcome = deps.guardrails.run_safe(state.get("answer", ""), ctx)
        _record_guard(state, "output", outcome)
        span.end(output=outcome.text[:500], blocked=outcome.blocked)
    state["answer"] = outcome.text
    if outcome.blocked:
        state["blocked"] = True
        state["stop_reason"] = "blocked"
    return state


def persist(state: AgentState, deps: AgentDeps) -> AgentState:
    deps.memory.remember_turn(state["thread_id"], "user", state["query"])
    deps.memory.remember_turn(
        state["thread_id"],
        "assistant",
        state.get("answer", ""),
        trace_id=state.get("trace_id"),
    )
    deps.tracer.event(
        "run.finished",
        trace_id=state.get("trace_id"),
        thread_id=state["thread_id"],
        stop_reason=state.get("stop_reason"),
        iterations=state.get("iteration"),
        tool_calls=state.get("tool_calls_made"),
        blocked=state.get("blocked"),
        latency_ms=round((time.time() - state.get("started_at", time.time())) * 1000, 2),
    )
    deps.tracer.flush()
    return state


def budget_exhausted(state: AgentState, deps: AgentDeps) -> str | None:
    """Returns a stop reason when a loop budget is spent, else None."""
    s = deps.settings
    if state.get("iteration", 0) >= s.agent_max_iterations:
        return "max_iterations"
    if state.get("tool_calls_made", 0) >= s.agent_max_tool_calls:
        return "max_tool_calls"
    elapsed = time.time() - state.get("started_at", time.time())
    if elapsed >= s.agent_wall_clock_seconds:
        return "timeout"
    return None
