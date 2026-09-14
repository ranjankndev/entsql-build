# Multi-agent composition

## First: you probably don't need this

One agent with more tools is cheaper, faster and much easier to evaluate. A
team multiplies model calls, latency, and the number of places an answer can go
wrong — and "which agent broke?" is a far harder question than "which tool
broke?".

Use a team only when workers genuinely differ:

| Reason | Example |
| --- | --- |
| Different **permissions** | one worker may issue refunds, another may only read |
| Different **models** | a cheap router/classifier, an expensive reasoner |
| Different **prompts and tools** that would otherwise fight for room in one system prompt | 30 tools in one prompt degrades tool choice |
| Different **owners** shipping on separate schedules | billing team and platform team |

If none of those apply, add a tool instead. That advice is in the module
docstring too, where someone reaching for `Supervisor` will actually read it.

## Shape

```
        guard_input (once)
              │
        ┌─────▼──────┐
        │   route    │  model choice, deterministic keyword fallback
        └─────┬──────┘
     ┌────────┴────────┐        each worker keeps its own
 ┌───▼────┐       ┌────▼───┐    tools, prompt, budgets and
 │ worker │       │ worker │    output guards
 └───┬────┘       └────┬───┘
     └────────┬────────┘
       ┌──────▼──────┐
       │ synthesize  │  or plain concatenation when synthesize=False
       └──────┬──────┘
        guard_output (once)
```

Flat by design: **no worker calls another worker.** A flat team is debuggable;
a graph of agents calling agents is where multi-agent projects go to die.

## One trace, not N

Every worker is re-pointed at the supervisor's tracer at construction and again
before each run, so a team run is a single trace with nested `worker.<name>`
spans. Without this you get N orphan traces and a timestamp-correlation
exercise at 3am.

```python
state = team.run("I was double charged for invoice 42")
state.trace_id          # one id for the whole team
state.routed_to         # ['billing']
state.usage             # tokens summed across workers and the synthesis call
```

## Bounds

| Bound | Where | Why |
| --- | --- | --- |
| `max_workers` | supervisor | caps fan-out per query — the cost multiplier |
| iterations / tool calls / wall clock | each worker's own `Settings` | a worker is a normal agent and keeps its own budgets |
| no recursion | structural | workers are agents, not supervisors |

## Failure behaviour

- A worker that errors returns `stop_reason="error"` in its `WorkerResult`; the
  team still answers with whatever the others produced.
- If **no** worker produced a usable answer, the team says so
  (`stop_reason="no_worker_answered"`) rather than inventing a synthesis of
  nothing.
- Routing failures fall back to deterministic keyword scoring — a router
  outage degrades routing quality, it does not take the team down.
- Blocked input never reaches a worker.

## Evaluating a team

`TeamState.as_state()` is shaped like an `AgentState`, so the existing harness
and scorers work unchanged:

```python
class TeamAdapter:
    def run(self, query, thread_id=None, user_id=None):
        return team.run(query, thread_id=thread_id).as_state()

report = run_suite("evals/datasets/core.jsonl", agent=TeamAdapter())
```

Add routing cases to your dataset: `expect_tools: ["billing"]` scores which
worker was consulted, because a team's first failure mode is routing to the
wrong specialist.

## Try it

```bash
python examples/support_team.py "I was double charged for invoice 42"
```

Runs with no credentials (the `echo` provider), and prints the route, the trace
id and the iteration count.
