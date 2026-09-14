"""LangGraph wiring over the *same* nodes as `agent/loop.py`.

Use this when you want LangGraph's checkpointing, streaming, interrupts or
Studio visualisation. Use `loop.py` when you want none of that and a stack
trace you can read. Behaviour is identical because the node functions are
shared — only the edges live here.

Graph:

    guard_input ──blocked──────────────────────────────────┐
         │                                                 │
     load_context ──► think ──tool_calls──► act ──► think   │
                        │                                   │
                     no tools                               │
                        ▼                                   │
                     reflect ──retry──► think                │
                        │                                    │
                       ok ▼                                  │
                    guard_output ◄───────────────────────────┘
                         │
                      persist ──► END
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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


def _after_guard_input(state: AgentState) -> str:
    return "guard_output" if state.get("blocked") else "load_context"


def _after_think(state: AgentState) -> str:
    return "reflect" if state.get("done") else "act"


def _after_act(state: AgentState) -> str:
    # Budgets are enforced in `_tick`, exactly as in the plain loop, so the
    # only reason to leave the cycle here is an approval pause.
    return "guard_output" if state.get("done") else "think"


def _after_reflect(state: AgentState, deps: AgentDeps) -> str:
    if state.get("done") or budget_exhausted(state, deps):
        return "guard_output"
    return "think"


def _bind(fn: Callable[[AgentState, AgentDeps], AgentState], deps: AgentDeps) -> Callable[[AgentState], AgentState]:
    """Bind deps into a single-argument node, so LangGraph never tries to pass
    its own `config` into our second parameter."""

    def node(state: AgentState) -> AgentState:
        return fn(state, deps)

    node.__name__ = getattr(fn, "__name__", "node")
    return node


def _bind_router(fn: Callable[[AgentState, AgentDeps], str], deps: AgentDeps) -> Callable[[AgentState], str]:
    def router(state: AgentState) -> str:
        return fn(state, deps)

    router.__name__ = getattr(fn, "__name__", "router")
    return router


def build_graph(deps: AgentDeps, checkpointer: Any | None = None) -> Any:
    """Compile the LangGraph app. `checkpointer` gives you durable threads —
    use `MemorySaver` locally and a Postgres/Cosmos saver in Azure."""
    from langgraph.graph import END, StateGraph

    builder: Any = StateGraph(AgentState)
    builder.add_node("guard_input", _bind(guard_input, deps))
    builder.add_node("load_context", _bind(load_context, deps))
    builder.add_node("think", _bind(_tick, deps))
    builder.add_node("act", _bind(act, deps))
    builder.add_node("reflect", _bind(reflect, deps))
    builder.add_node("guard_output", _bind(guard_output, deps))
    builder.add_node("persist", _bind(persist, deps))

    builder.set_entry_point("guard_input")
    builder.add_conditional_edges(
        "guard_input", _after_guard_input, {"load_context": "load_context", "guard_output": "guard_output"}
    )
    builder.add_edge("load_context", "think")
    builder.add_conditional_edges(
        "think", _after_think, {"act": "act", "reflect": "reflect"}
    )
    builder.add_conditional_edges(
        "act", _after_act, {"think": "think", "guard_output": "guard_output"}
    )
    builder.add_conditional_edges(
        "reflect", _bind_router(_after_reflect, deps), {"think": "think", "guard_output": "guard_output"}
    )
    builder.add_edge("guard_output", "persist")
    builder.add_edge("persist", END)

    # `interrupt_before=["act"]` turns every tool call into a human approval
    # gate; keep it for high-risk deployments.
    return builder.compile(checkpointer=checkpointer)


def _tick(state: AgentState, deps: AgentDeps) -> AgentState:
    """`think` plus loop accounting, so budgets apply in the graph too."""
    stop = budget_exhausted(state, deps)
    if stop:
        state["done"] = True
        state["stop_reason"] = stop
        state["answer"] = state.get("answer") or f"I stopped early: {stop.replace('_', ' ')}."
        return state
    state["iteration"] = state.get("iteration", 0) + 1
    return think(state, deps)


def run_graph(
    deps: AgentDeps,
    query: str,
    thread_id: str | None = None,
    user_id: str | None = None,
    checkpointer: Any | None = None,
) -> AgentState:
    app = build_graph(deps, checkpointer)
    state = new_state(query, thread_id, user_id)
    config = {
        "configurable": {"thread_id": state["thread_id"]},
        # A hard backstop below LangGraph's default, derived from the same
        # budget the plain loop uses. Belt and braces: `_tick` should stop first.
        "recursion_limit": max(8, deps.settings.agent_max_iterations * 3 + 6),
    }
    return app.invoke(state, config=config)
