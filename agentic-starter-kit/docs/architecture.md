# Architecture

## One run, node by node

| Node | File | What it does | Failure behaviour |
| --- | --- | --- | --- |
| `guard_input` | `agent/nodes.py` | runs INPUT guards over the user text | blocked → jump to `guard_output`, `stop_reason="blocked"` |
| `load_context` | `agent/nodes.py` | builds the prompt from the three memory layers | — |
| `think` | `agent/nodes.py` | one model call: tool request or final answer | transient failures are retried with backoff in the LLM seam; an open circuit or a permanent error → `stop_reason="error"`, caller still gets a reply |
| `act` | `agent/nodes.py` | guards each call, runs the tool, guards the result | tool exception becomes an observation, loop continues |
| `reflect` | `agent/nodes.py` | optional self-check; may reopen the loop | off by default (`--reflect` to enable) |
| `guard_output` | `agent/nodes.py` | runs OUTPUT guards over the answer | blocked → policy message |
| `persist` | `agent/nodes.py` | writes turns to memory, emits `run.finished` | — |

## Budgets

Three, all enforced in `budget_exhausted` and checked every iteration:

| Setting | Default | Stops |
| --- | --- | --- |
| `AGENT_MAX_ITERATIONS` | 8 | think→act cycles |
| `AGENT_MAX_TOOL_CALLS` | 16 | total tool invocations |
| `AGENT_WALL_CLOCK_SECONDS` | 120 | elapsed time |

When one trips, the agent returns what it has with an honest `stop_reason`. It
never returns a confident answer it did not finish deriving.

## Plain loop vs. LangGraph

Both drive the **same node functions**. `loop.py` is a `while` loop you can
step through in a debugger; `graph.py` is the LangGraph wiring.

| Use `loop.py` | Use `graph.py` |
| --- | --- |
| simplest possible stack trace | durable threads via a checkpointer |
| no extra dependency | streaming intermediate steps |
| easiest to unit test | `interrupt_before=["act"]` for human approval |
| default for the CLI and API | LangGraph Studio visualisation |

```bash
starter chat "..." --graph     # same behaviour, LangGraph edges
```

```python
from langgraph.checkpoint.memory import MemorySaver
from starter.agent.graph import build_graph

app = build_graph(deps, checkpointer=MemorySaver())
app.invoke(state, config={"configurable": {"thread_id": "t1"}})
```

If you add a node, add it to `loop._drive` **and** `graph.build_graph`. The
node itself stays framework-free — that is the point of the seam, and it is
what makes porting to another orchestrator a day's work rather than a rewrite.

## Streaming

`Agent.stream(query, ...)` is the **only** driver; `Agent.run` drains it. A
blocking caller and a streaming caller therefore cannot drift apart — the test
`test_stream_and_run_agree` pins that.

Events (`agent/events.py`), delivered over SSE by `POST /chat/stream`:

| Event | When | Notes |
| --- | --- | --- |
| `status` | stage change | `guard_input`, `load_context`, `thinking`, `budget_exhausted`, `guard_output`, `awaiting_approval` |
| `guardrail` | a guard warns or blocks | passing no-op guards are not narrated |
| `tool` | per call | `phase: start` with args, `phase: end` with `ok` |
| `token` | draft text delta | **only** when `AGENT_STREAM_TOKENS=true`; always `provisional: true` |
| `answer` | after `guard_output` | the text the guardrails actually approved |
| `error` | provider or node failure | followed by an `answer` and a `done` |
| `done` | always last | thread id, trace id, stop reason, counters, citations |

### Why tokens are off by default

Output guardrails cannot unsend a token. Streaming raw model output means PII,
a leaked secret or an unsafe answer reaches the user *before* any guard sees
it. So the default streams progress, not prose, and emits the answer once —
after `guard_output`.

Turn tokens on when latency perception matters more than the output guards
(internal tools, low-risk domains), and make the client replace all `token`
text with the final `answer` text. The kit marks every token `provisional` so
that contract is impossible to miss.

```bash
curl -N -X POST localhost:8000/chat/stream \
  -H 'content-type: application/json' -d '{"query":"what is the sla?"}'
```

```bash
starter chat "what is the sla?" --stream    # events on stderr, answer on stdout
```

## Human-in-the-loop

Two mechanisms, use either or both:

1. **Tool-level** — `requires_approval=True` on the tool. `act` stops the run
   with `stop_reason="awaiting_approval"` and `pending_approval` set. Resume by
   re-running with the tool name in `state["approved_tools"]`.
2. **Graph-level** — compile with `interrupt_before=["act"]` and a
   checkpointer. Every tool call becomes a checkpoint you can inspect, edit and
   resume.

## Extension points

| You want to | Change |
| --- | --- |
| add a tool | `tools/examples.py` (or your own module) + allow-list in `config/guardrails.yaml` |
| change the persona or policy | `agent/prompts.py` |
| add a policy check | `register_guard` + `config/guardrails.yaml` |
| swap the model | `LLM_PROVIDER` in `.env` |
| swap the tracer | implement `Tracer`, return it from `get_tracer` |
| swap memory storage | implement `MemoryStore` |
| add a planning step | new node in `nodes.py`, wired into both drivers |
| multi-agent | `agent/team.py` — `Supervisor` + `Worker`, one shared tracer; read [`docs/multi-agent.md`](multi-agent.md) first, because one agent with more tools usually wins |

## What is deliberately not here

No vector database, no RAG pipeline, no multi-agent orchestration, no
streaming. They are all project decisions, and a starter kit that guesses them
wrong costs more than one that leaves the seam clean. The seams they would plug
into — `MemoryStore.search_records`, `ToolRegistry`, `AgentDeps` — are all in
place.
