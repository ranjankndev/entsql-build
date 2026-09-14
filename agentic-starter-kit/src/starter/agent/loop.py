"""The agent loop, with no orchestration framework required.

    guard_input -> load_context -> ( think -> act )* -> reflect -> guard_output -> persist

Every exit is explicit and recorded in `stop_reason`: answered, max_iterations,
max_tool_calls, timeout, blocked, awaiting_approval, error. An agent that can
loop forever is an incident waiting to happen; this one cannot.
"""

from __future__ import annotations

from dataclasses import dataclass

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

    def run(
        self, query: str, thread_id: str | None = None, user_id: str | None = None
    ) -> AgentState:
        state = new_state(query, thread_id, user_id)
        with trace_run(
            "agent.run",
            tracer=self.deps.tracer,
            thread_id=state["thread_id"],
            user_id=user_id,
        ) as span:
            state["trace_id"] = span.trace_id
            try:
                self._drive(state)
            except Exception as exc:
                state["error"] = f"{type(exc).__name__}: {exc}"
                state["stop_reason"] = "error"
                state["answer"] = state.get("answer") or "Something went wrong on my side."
            span.end(output=state.get("answer", "")[:500], stop_reason=state.get("stop_reason"))
        return state

    def _drive(self, state: AgentState) -> AgentState:
        guard_input(state, self.deps)
        if state.get("blocked"):
            return persist(state, self.deps)

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
                break
            state["iteration"] = state.get("iteration", 0) + 1
            think(state, self.deps)
            if state.get("done"):
                reflect(state, self.deps)  # may reopen the loop
                continue
            act(state, self.deps)

        guard_output(state, self.deps)
        return persist(state, self.deps)


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
