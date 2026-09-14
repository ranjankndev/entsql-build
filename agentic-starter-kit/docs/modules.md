# Module catalogue

Each entry: what it is, the public surface, how to use it **standalone** in an
existing project, and the seam you extend.

---

## 1. Guardrails — `src/starter/guardrails/`

**What** Ordered policy checks at three stages: `INPUT` (user text), `TOOL`
(arguments before a call, results after), `OUTPUT` (final answer). A guard
returns a `GuardResult`; the pipeline decides whether that blocks, warns, or
rewrites the text.

**Surface** `build_pipeline`, `GuardContext`, `Stage`, `GuardResult`,
`GuardrailViolation`, `register_guard`.

**Standalone**

```python
from starter.guardrails import GuardContext, Stage, build_pipeline

pipeline = build_pipeline("config/guardrails.yaml")
outcome = pipeline.run_safe(text, GuardContext(stage=Stage.INPUT, thread_id="t1"))
if outcome.blocked:
    return outcome.text          # policy message
text = outcome.text              # possibly redacted
```

Copy `guardrails/` + `settings.py`; the only third-party import is `pyyaml`.

**Extend** — register a factory, then name it in the YAML:

```python
from starter.guardrails import GuardResult, Stage, register_guard

@register_guard("toxicity")
class ToxicityGuard:
    name = "toxicity"
    stages = (Stage.OUTPUT,)
    def __init__(self, threshold: float = 0.8): self.threshold = threshold
    def check(self, text, ctx):
        score = my_classifier(text)
        return GuardResult.ok(self.name) if score < self.threshold \
            else GuardResult.fail(self.name, f"toxicity {score:.2f}")
```

**Model-based guards** `groundedness` (is the answer supported by the tool
results?) and `llm_judge` (any written rubric) live behind the same registry,
with deterministic sampling, verdict caching, injection-fenced prompts and an
explicit `on_error` fail-open/closed choice. `set_default_judge(provider)`
injects the model; `build_agent` does it for you.

**Depends on** `settings`, `pyyaml`. The model-based guards additionally need
`starter.llm`; the regex guards do not.

---

## 2. Evaluation — `src/starter/evals/` + `evals/`

**What** `EvalCase` → run through any agent → `Score` list → `EvalReport` with
pass rate, per-scorer means, p95 latency, markdown + JSON output, and scores
pushed to the tracer against the run's trace id.

**Surface** `run_suite`, `load_cases`, `EvalCase`, `EvalReport`, `write_report`,
`SCORERS`, `llm_judge`, `compare`, `save_baseline`, `load_report`.

**Standalone** — the harness only needs an object with
`.run(query, thread_id=None, user_id=None)` returning a dict-like state with
`answer`, `tool_results`, `guardrail_events`, `iteration`, `stop_reason`:

```python
from starter.evals import run_suite
report = run_suite("cases.jsonl", agent=MyAgentAdapter())
print(report.to_markdown())
assert report.pass_rate >= 0.9
```

**Diffing** `compare(baseline, current)` returns regressions, fixes, added and
removed cases, per-scorer deltas and the latency change — because two cases
flipping in opposite directions leave the pass rate unchanged:

```python
from starter.evals import compare, load_report, run_suite, save_baseline

report = run_suite("evals/datasets/core.jsonl")
diff = compare(load_report("evals/baselines/core.json"), report.to_dict())
assert diff.clean, diff.to_markdown()
```

**Extend** — any callable `(case, state) -> Score` is a scorer; pass it in
`extra_scorers={"name": fn}` or add it to `SCORERS`.

**Depends on** `agent` (only for the default agent), `observability`.

---

## 3. Context memory — `src/starter/memory/`

**What** Three layers assembled in a fixed, documented order, with rolling
summarisation when the window overflows.

**Surface** `ContextMemory` (`remember_turn`, `remember_fact`, `recall`,
`build_context`, `maybe_summarize`), `InMemoryStore`, `FileMemoryStore`,
`CosmosMemoryStore`, `build_memory`.

**Standalone**

```python
from starter.memory import ContextMemory, FileMemoryStore

memory = ContextMemory(store=FileMemoryStore(".memory"), max_turns=20, summary_trigger=12, llm=my_llm)
memory.remember_fact("user-42", "Prefers answers in metric units.", key="units")
messages = memory.build_context("thread-1", "How far is it?", owner_id="user-42")
```

**Vector / RAG** `VectorMemoryStore` decorates any store with hybrid retrieval
(`alpha * cosine + (1 - alpha) * lexical`); `AzureAISearchStore` pushes the same
query server-side. The embedder is its own seam (`hashing` offline by default,
`openai`/`azure_openai` for real semantics).

```python
from starter.memory import HashingEmbedder, InMemoryStore, VectorMemoryStore

store = VectorMemoryStore(inner=InMemoryStore(), embedder=HashingEmbedder(256), alpha=0.7)
```

**Extend** — implement the `MemoryStore` protocol for Redis, pgvector, Azure AI
Search. Only `search_records` needs to change to move from lexical to vector
retrieval; nothing above it does.

**Depends on** `settings`. `llm` only for LLM summarisation (there is a
deterministic fallback).

---

## 4. Observability — `src/starter/observability/`

**What** A `Tracer` protocol with spans (`span`, `generation`, `tool`, `guard`),
run-level scores, and events. `NoOpTracer` writes one JSON line per span;
`LangfuseTracer` also ships to Langfuse and degrades to logs on any failure.

**Surface** `get_tracer`, `trace_run`, `Tracer`, `Span`, `NoOpTracer`.

**Standalone**

```python
from starter.observability import get_tracer, trace_run

tracer = get_tracer()
with trace_run("my.pipeline", tracer=tracer, user_id="u1") as root:
    with tracer.span("retrieval", kind="tool", input=query) as s:
        docs = retrieve(query)
        s.end(output=f"{len(docs)} docs", hit_count=len(docs))
tracer.score(root.trace_id, "relevance", 0.92)
```

**Extend** — implement the protocol for OpenTelemetry, Arize Phoenix, W&B
Weave, or LangSmith; nothing else changes.

**Depends on** `settings`. Langfuse SDK optional.

---

## 5. Agent loop — `src/starter/agent/`

**What** `nodes.py` holds the behaviour as `(state, deps) -> state` functions.
`loop.py` drives them with a plain `while` and hard budgets. `graph.py` wires
*the same functions* into LangGraph for checkpointing, streaming and interrupts.

**Surface** `build_agent`, `Agent.run`, `Agent.stream`, `AgentEvent`, `AgentDeps`,
`AgentState`, `build_graph`, `run_graph`.

**Standalone** — inject your own pieces:

```python
from starter.agent import Agent, AgentDeps

agent = Agent(AgentDeps(llm=my_llm, tools=my_registry, memory=my_memory,
                        guardrails=my_pipeline, tracer=my_tracer, settings=my_settings))
state = agent.run("question", thread_id="t1")
```

**Extend** — add a node in `nodes.py`, then add it to `loop._drive` *and* an
edge in `graph.build_graph`. Keep both in sync; the tests compare behaviour.

**Depends on** everything else. This is the only module that does.

---

## 5b. Multi-agent — `src/starter/agent/team.py`

**What** `Supervisor` routes a query to `Worker`s (each a normal `Agent`),
bounds fan-out with `max_workers`, synthesises one answer, and puts the whole
team on **one trace**. Routing asks the model and falls back to deterministic
keyword scoring, so it runs offline.

**Standalone**

```python
from starter.agent.team import Supervisor, Worker

team = Supervisor(deps=supervisor_agent.deps, max_workers=2, workers=[
    Worker("billing", "Refunds, invoices and charges.", billing_agent),
    Worker("technical", "Errors, outages and API problems.", technical_agent),
])
state = team.run("I was double charged")   # state.as_state() scores in the eval harness
```

**Before you use it** — read [`docs/multi-agent.md`](multi-agent.md). One agent
with more tools is cheaper and easier to evaluate; a team earns its cost only
when workers differ in permissions, model, prompt/tools or ownership.

## 6. Tools — `src/starter/tools/`

**What** `Tool` = name + JSON schema + callable + `risk` + `requires_approval`.
`ToolRegistry` times calls, converts exceptions into observations (a tool bug
never kills the loop), and emits OpenAI-style schemas.

**Standalone**

```python
from starter.tools import ToolRegistry, tool

@tool(name="lookup_order", description="Fetch an order by id.",
      parameters={"type": "object", "properties": {"order_id": {"type": "string"}},
                  "required": ["order_id"]},
      risk="read")
def lookup_order(order_id: str) -> str: ...

registry = ToolRegistry().add(lookup_order)
```

Anything with `risk` above `read` should be in the guardrail allow-list and
usually `requires_approval=True`.

---

## 7. LLM seam — `src/starter/llm/`

**What** One `complete(messages, tools) -> LLMResponse` call, plus an optional
`stream()` that yields text deltas then the assembled response;
`stream_or_complete` degrades gracefully for providers without it. Providers:
`echo` (offline, scriptable — the reason the test suite needs no keys),
`openai`, `azure_openai`, `anthropic` (all via LangChain chat models).

**Resilience** `ResilientProvider` wraps any provider with exponential backoff
+ full jitter, `Retry-After` support, and a circuit breaker; `get_llm` applies
it automatically to the network-backed providers. Transient (429/5xx/timeout/
connection) is retried, permanent (400/auth/content filter) never is, and after
`LLM_BREAKER_THRESHOLD` consecutive failures the breaker opens and calls fail
fast with `LLMUnavailable` until `LLM_BREAKER_RESET_SECONDS` elapses. Streaming
is retried only before the first delta reaches the caller — after that, a retry
would duplicate emitted text.

```python
from starter.llm import CircuitBreaker, ResilientProvider, RetryPolicy

provider = ResilientProvider(
    inner=my_provider,
    policy=RetryPolicy(max_attempts=4, base_delay=0.5, max_delay=20),
    breaker=CircuitBreaker(failure_threshold=5, reset_seconds=30),
)
```

`sleep` and `now` are injectable, which is why the tests assert the exact
backoff curve without waiting for it.

**Extend** — add a branch in `get_llm`, or pass any object with `.complete()`.
`EchoProvider(scripted=[LLMResponse(...)])` is how the loop tests drive exact
tool-call sequences.

---

## 8. API — `src/starter/api/`

`POST /chat`, `POST /chat/stream` (server-sent events), `GET /healthz`,
`GET /readyz`, `POST /guardrails/check`.
Thin by design: it constructs the agent once and translates state to JSON.
The probes are what Container Apps uses for liveness/readiness.

---

## 9. Rate limits — `src/starter/ratelimit.py`

**What** Per-identity token bucket (rate + burst) plus fixed-window daily
request and token quotas. Framework-free; the API is a thin adapter.

**Standalone**

```python
from starter.ratelimit import RateLimited, RateLimiter

limits = RateLimiter(requests_per_minute=30, burst=5, tokens_per_day=200_000)
limits.enforce(identity)                      # raises RateLimited
limits.record_usage(identity, tokens_used)    # charged to the next check
```

**Extend** — implement `QuotaStore` (`incr`, `get`, `reset`) against Redis or
Cosmos DB; the in-memory default is per replica. Details and the reasoning in
[`docs/limits.md`](limits.md).

## 10. Deployment — `deploy/azure/`

Bicep for Container Apps + Azure OpenAI + Cosmos DB + Key Vault + managed
identity + Log Analytics, deploy scripts, and the full guide in
[`deploy/azure/README.md`](../deploy/azure/README.md).
