"""The agent loop, with no orchestration framework required.

    guard_input -> load_context -> ( think -> act )* -> reflect -> guard_output -> persist

Every exit is explicit and recorded in `stop_reason`: answered, max_iterations,
max_tool_calls, timeout, blocked, awaiting_approval, error. An agent that can
loop forever is an incident waiting to happen; this one cannot.

`Agent.stream` is the single driver; `Agent.run` drains it. Blocking and
streaming callers therefore cannot drift apart in behaviour.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from starter.agent.events import AgentEvent
from starter.agent.nodes import (
    AgentDeps,
    act,
    budget_exhausted,
    guard_input,
    guard_output,
    load_context,
    persist,
    reflect,
    think,
    think_streaming,
)
from starter.agent.state import AgentState, new_state
from starter.guardrails import build_pipeline
from starter.llm import get_llm
from starter.memory import build_memory
from starter.observability import get_tracer, trace_run
from starter.settings import Settings, get_settings
from starter.tools import default_registry


@dataclass
class Agent:
    deps: AgentDeps

    # ------------------------------------------------------------- blocking

    def run(
        self, query: str, thread_id: str | None = None, user_id: str | None = None
    ) -> AgentState:
        state = new_state(query, thread_id, user_id)
        for _ in self.stream(query, thread_id, user_id, state=state):
            pass
        return state

    # ------------------------------------------------------------ streaming

    def stream(
        self,
        query: str,
        thread_id: str | None = None,
        user_id: str | None = None,
        state: AgentState | None = None,
    ) -> Iterator[AgentEvent]:
        """Drive one run, yielding progress events. See `agent/events.py` for
        why the answer is only emitted after the output guardrails pass."""
        state = state if state is not None else new_state(query, thread_id, user_id)
        with trace_run(
            "agent.run",
            tracer=self.deps.tracer,
            thread_id=state["thread_id"],
            user_id=state.get("user_id"),
        ) as span:
            state["trace_id"] = span.trace_id
            try:
                yield from self._drive(state)
            except Exception as exc:
                state["error"] = f"{type(exc).__name__}: {exc}"
                state["stop_reason"] = "error"
                state["answer"] = state.get("answer") or "Something went wrong on my side."
                yield AgentEvent.error(state["error"])
                yield AgentEvent.answer(state["answer"], error=True)
            span.end(output=state.get("answer", "")[:500], stop_reason=state.get("stop_reason"))
            yield AgentEvent.done(dict(state))

    # ---------------------------------------------------------------- inner

    def _drive(self, state: AgentState) -> Iterator[AgentEvent]:
        yield AgentEvent.status("guard_input")
        guard_input(state, self.deps)
        yield from _guardrail_events(state, "input")
        if state.get("blocked"):
            persist(state, self.deps)
            yield AgentEvent.answer(state.get("answer", ""), blocked=True)
            return

        yield AgentEvent.status("load_context")
        load_context(state, self.deps)

        while not state.get("done"):
            stop = budget_exhausted(state, self.deps)
            if stop:
                state["done"] = True
                state["stop_reason"] = stop
                state["answer"] = state.get("answer") or (
                    "I stopped before finishing: "
                    f"{stop.replace('_', ' ')}. Here is what I have so far:\n"
                    + "\n".join(state.get("scratchpad", []))
                )
                yield AgentEvent.status("budget_exhausted", reason=stop)
                break

            state["iteration"] = state.get("iteration", 0) + 1
            yield AgentEvent.status("thinking", iteration=state["iteration"])
            if self.deps.settings.agent_stream_tokens:
                for delta in think_streaming(state, self.deps):
                    yield AgentEvent.token(delta)
            else:
                think(state, self.deps)

            if state.get("done"):
                reflect(state, self.deps)  # may reopen the loop
                continue

            for call in state.get("pending_tool_calls", []):
                yield AgentEvent.tool(call["name"], "start", args=call.get("args", {}))
            act(state, self.deps)
            yield from _guardrail_events(state, "tool")
            for result in state.get("tool_results", [])[-self.deps.settings.agent_max_tool_calls :]:
                yield AgentEvent.tool(result["tool"], "end", ok=result["ok"])
            if state.get("stop_reason") == "awaiting_approval":
                yield AgentEvent.status("awaiting_approval", **(state.get("pending_approval") or {}))

        yield AgentEvent.status("guard_output")
        guard_output(state, self.deps)
        yield from _guardrail_events(state, "output")
        persist(state, self.deps)
        yield AgentEvent.answer(state.get("answer", ""), blocked=bool(state.get("blocked")))


def _guardrail_events(state: AgentState, stage_prefix: str) -> Iterator[AgentEvent]:
    """Emit the guardrail verdicts recorded since the last emission."""
    seen = state.get("events_emitted", 0)
    events = state.get("guardrail_events", [])
    for event in events[seen:]:
        if event["passed"] and event["severity"] == "info":
            continue  # don't narrate every no-op guard
        yield AgentEvent.guardrail(
            event["stage"], event["guard"], event["message"], not event["passed"]
        )
    state["events_emitted"] = len(events)


def build_agent(
    settings: Settings | None = None,
    agent_name: str = "Assistant",
    domain: str = "general questions",
    reflect_enabled: bool = False,
    deps: AgentDeps | None = None,
) -> Agent:
    """Wire the default stack. Override any single piece by passing `deps`."""
    if deps is not None:
        return Agent(deps)
    s = settings or get_settings()
    llm = get_llm(s)
    return Agent(
        AgentDeps(
            llm=llm,
            tools=default_registry(),
            memory=build_memory(s, llm=llm),
            guardrails=build_pipeline(settings=s),
            tracer=get_tracer(s),
            settings=s,
            agent_name=agent_name,
            domain=domain,
            reflect=reflect_enabled,
        )
    )
