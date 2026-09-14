"""Supervisor + workers.

**Read this before you use it.** Multi-agent is usually the wrong first answer.
One agent with more tools is cheaper, faster, and far easier to evaluate: a
team multiplies your model calls, your latency and the number of places an
answer can go wrong, and "which agent broke?" is a much harder question than
"which tool broke?". Reach for a team only when workers genuinely differ:

* different **permissions** (one may write, one may only read),
* different **models** (a cheap classifier, an expensive reasoner),
* different **prompts and tools** that would otherwise fight for room in one
  system prompt,
* different **owners** shipping on different schedules.

If none of those apply, add a tool instead.

What this module gives you, and what it deliberately refuses:

* **One trace for the whole team.** Every worker shares the supervisor's
  tracer, so a run is one trace with nested worker spans — not N orphan traces
  you have to correlate by timestamp.
* **Bounded fan-out.** `max_workers` caps how many workers one query may
  consult; each worker keeps its own iteration and tool budgets. There is no
  worker-calls-worker recursion: a flat team is debuggable, a graph of agents
  calling agents is not.
* **Deterministic routing fallback.** Routing asks the model, but falls back to
  keyword scoring over worker descriptions, so the team runs offline and its
  tests are reproducible.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from starter.agent.events import AgentEvent
from starter.agent.loop import Agent
from starter.agent.nodes import AgentDeps, guard_input, guard_output
from starter.agent.state import AgentState, new_state
from starter.observability import trace_run

_WORD = re.compile(r"[a-z0-9]+")

ROUTER_PROMPT = """\
Pick the specialists that should answer the request. Choose as few as possible.

Specialists:
{roster}

Request: {query}

Reply with only a comma-separated list of specialist names, or NONE.
"""

SYNTHESIS_PROMPT = """\
Combine the specialist answers below into one reply to the user. Keep the
citations. Do not add facts that no specialist reported. If they disagree, say
so plainly rather than picking one silently.

Request: {query}

{sections}
"""


@dataclass
class Worker:
    """One specialist. `description` is what the router reads, so write it as
    the sentence you would put in a runbook: what it covers, and what it does
    not."""

    name: str
    description: str
    agent: Agent

    def keywords(self) -> set[str]:
        return set(_WORD.findall(f"{self.name} {self.description}".lower()))


@dataclass
class WorkerResult:
    worker: str
    answer: str
    stop_reason: str
    blocked: bool
    iterations: int
    tool_calls: int
    citations: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None


@dataclass
class TeamState:
    query: str
    thread_id: str
    trace_id: str
    answer: str = ""
    routed_to: list[str] = field(default_factory=list)
    results: list[WorkerResult] = field(default_factory=list)
    stop_reason: str = ""
    blocked: bool = False
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0

    def as_state(self) -> dict[str, Any]:
        """Shaped like an `AgentState` so evals and scorers work unchanged."""
        return {
            "query": self.query,
            "thread_id": self.thread_id,
            "trace_id": self.trace_id,
            "answer": self.answer,
            "iteration": sum(r.iterations for r in self.results),
            "tool_calls_made": sum(r.tool_calls for r in self.results),
            "tool_results": [
                {"tool": r.worker, "ok": not r.blocked and r.error is None, "content": r.answer}
                for r in self.results
            ],
            "citations": [c for r in self.results for c in r.citations],
            "guardrail_events": [],
            "stop_reason": self.stop_reason,
            "blocked": self.blocked,
            "usage": self.usage,
            "error": None,
        }


@dataclass
class Supervisor:
    """Routes a query to workers and synthesises one answer.

    `deps` supplies the supervisor's own model, guardrails and tracer. Worker
    agents keep their own tools and prompts but are re-pointed at the
    supervisor's tracer, so the whole team lands on one trace.
    """

    deps: AgentDeps
    workers: list[Worker] = field(default_factory=list)
    max_workers: int = 2
    synthesize: bool = True

    def __post_init__(self) -> None:
        names = [w.name for w in self.workers]
        if len(names) != len(set(names)):
            raise ValueError(f"worker names must be unique: {names}")
        self.share_tracer()

    def share_tracer(self) -> None:
        """Point every worker at the supervisor's tracer.

        Done at construction (and again before each worker runs, in case the
        tracer was swapped since), so a team run is one trace with nested
        worker spans rather than N orphan traces to correlate by timestamp.
        """
        for worker in self.workers:
            worker.agent.deps.tracer = self.deps.tracer

    # --------------------------------------------------------------- routing

    def route(self, query: str) -> list[Worker]:
        by_name = {w.name.lower(): w for w in self.workers}
        chosen: list[Worker] = []
        try:
            roster = "\n".join(f"- {w.name}: {w.description}" for w in self.workers)
            reply = self.deps.llm.complete(
                [
                    {
                        "role": "user",
                        "content": ROUTER_PROMPT.format(roster=roster, query=query),
                    }
                ]
            ).text
            for token in re.split(r"[,\n]", reply):
                worker = by_name.get(token.strip().lower())
                if worker is not None and worker not in chosen:
                    chosen.append(worker)
        except Exception:
            chosen = []
        if not chosen:
            chosen = self._keyword_route(query)
        return chosen[: self.max_workers]

    def _keyword_route(self, query: str) -> list[Worker]:
        """Deterministic fallback: overlap between the query and each worker's
        description. Keeps the team runnable (and testable) with no model."""
        tokens = set(_WORD.findall(query.lower()))
        scored = [(len(tokens & w.keywords()), w) for w in self.workers]
        ranked = [w for score, w in sorted(scored, key=lambda p: p[0], reverse=True) if score]
        return ranked or self.workers[:1]

    # ------------------------------------------------------------- execution

    def run(self, query: str, thread_id: str | None = None, user_id: str | None = None) -> TeamState:
        started = time.perf_counter()
        base = new_state(query, thread_id, user_id)
        team = TeamState(query=query, thread_id=base["thread_id"], trace_id="")

        with trace_run(
            "team.run", tracer=self.deps.tracer, thread_id=team.thread_id, user_id=user_id
        ) as span:
            team.trace_id = span.trace_id
            base["trace_id"] = span.trace_id

            guard_input(base, self.deps)
            if base.get("blocked"):
                team.answer, team.blocked, team.stop_reason = base["answer"], True, "blocked"
                span.end(output=team.answer[:200], stop_reason="blocked")
                team.latency_ms = (time.perf_counter() - started) * 1000
                return team

            with self.deps.tracer.span("team.route", kind="span", input=query) as route_span:
                selected = self.route(base["query"])
                team.routed_to = [w.name for w in selected]
                route_span.end(output=", ".join(team.routed_to) or "none")

            for worker in selected:
                team.results.append(self._run_worker(worker, base["query"], team))

            team.answer = self._synthesize(base["query"], team)
            final = dict(base)
            final["answer"] = team.answer
            final["tool_results"] = team.as_state()["tool_results"]
            guard_output(final, self.deps)  # type: ignore[arg-type]
            team.answer = final["answer"]
            team.blocked = bool(final.get("blocked"))
            team.stop_reason = "blocked" if team.blocked else (team.stop_reason or "answered")
            span.end(output=team.answer[:500], stop_reason=team.stop_reason)

        team.latency_ms = (time.perf_counter() - started) * 1000
        return team

    def _run_worker(self, worker: Worker, query: str, team: TeamState) -> WorkerResult:
        # One trace for the team: the worker borrows the supervisor's tracer.
        worker.agent.deps.tracer = self.deps.tracer
        with self.deps.tracer.span(f"worker.{worker.name}", kind="span", input=query) as span:
            state: AgentState = worker.agent.run(query, thread_id=f"{team.thread_id}:{worker.name}")
            span.end(
                output=state.get("answer", "")[:500],
                stop_reason=state.get("stop_reason"),
                iterations=state.get("iteration"),
            )
        for key, value in (state.get("usage") or {}).items():
            if isinstance(value, int):
                team.usage[key] = team.usage.get(key, 0) + value
        return WorkerResult(
            worker=worker.name,
            answer=state.get("answer", ""),
            stop_reason=state.get("stop_reason", ""),
            blocked=bool(state.get("blocked")),
            iterations=state.get("iteration", 0),
            tool_calls=state.get("tool_calls_made", 0),
            citations=state.get("citations", []),
            usage=dict(state.get("usage", {})),
            error=state.get("error"),
        )

    def _synthesize(self, query: str, team: TeamState) -> str:
        usable = [r for r in team.results if r.answer and not r.blocked]
        if not usable:
            team.stop_reason = "no_worker_answered"
            return "I couldn't get an answer from any specialist for that request."
        if len(usable) == 1 or not self.synthesize:
            return "\n\n".join(f"**{r.worker}**: {r.answer}" for r in usable)

        sections = "\n\n".join(f"### {r.worker}\n{r.answer}" for r in usable)
        with self.deps.tracer.span("team.synthesize", kind="generation") as span:
            reply = self.deps.llm.complete(
                [
                    {
                        "role": "user",
                        "content": SYNTHESIS_PROMPT.format(query=query, sections=sections),
                    }
                ]
            )
            span.end(output=reply.text[:500], usage=reply.usage)
        for key, value in (reply.usage or {}).items():
            if isinstance(value, int):
                team.usage[key] = team.usage.get(key, 0) + value
        return reply.text

    # ------------------------------------------------------------- streaming

    def stream(self, query: str, thread_id: str | None = None, user_id: str | None = None):
        """Progress events for a team run, reusing the agent event vocabulary."""
        team = self.run(query, thread_id, user_id)
        yield AgentEvent.status("routed", workers=team.routed_to)
        for result in team.results:
            yield AgentEvent.tool(result.worker, "end", ok=not result.blocked)
        yield AgentEvent.answer(team.answer, blocked=team.blocked)
        yield AgentEvent.done(team.as_state())
