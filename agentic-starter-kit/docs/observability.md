# Observability

## The model

One `Tracer`, four span kinds:

| Kind | Emitted by | Carries |
| --- | --- | --- |
| `span` | the run, memory assembly | duration, stop reason |
| `generation` | every model call | prompt, output, tool calls requested, token usage |
| `tool` | every tool invocation | args, result, ok/failed, duration |
| `guard` | each guardrail stage | which guards ran, verdicts, whether it blocked |

Everything in one run shares a `trace_id`, and `run_suite` pushes eval scores
against that same id — so a failing eval case in CI links to the exact trace.

## Langfuse

```bash
OBSERVABILITY_PROVIDER=langfuse
LANGFUSE_PUBLIC_KEY=pk-...
LANGFUSE_SECRET_KEY=sk-...
LANGFUSE_HOST=https://cloud.langfuse.com   # or your self-hosted URL
```

Self-host it on Container Apps with a Postgres flexible server when transcripts
may not leave your tenancy — the config is the same, only `LANGFUSE_HOST`
changes. If the SDK or the credentials are missing, `LangfuseTracer` logs a
warning and degrades to structured JSON logs. Observability must never be the
thing that takes the service down.

## Without a vendor

`NoOpTracer` emits one JSON line per span to the `starter.trace` logger.
Container Apps ships that to Log Analytics, and you can answer real questions:

```kusto
ContainerAppConsoleLogs_CL
| where Log_s has "run.finished"
| extend p = parse_json(Log_s)
| summarize runs = count(),
            blocked = countif(p.blocked == true),
            exhausted = countif(p.stop_reason == "max_iterations"),
            p95_ms = percentile(todouble(p.latency_ms), 95)
          by bin(TimeGenerated, 15m)
```

## The five metrics that matter

| Metric | Where it comes from | Why |
| --- | --- | --- |
| Guardrail block rate | `guardrail_events` | a spike is an attack or an over-tight policy — both need you |
| Loop exhaustion rate | `stop_reason == max_iterations` | the agent is thrashing; cost and latency follow |
| Tool error rate | `tool_results[].ok` | your dependency is down before your users tell you |
| p95 end-to-end latency | `run.finished.latency_ms` | model throttling shows up here first |
| Tokens (or cost) per resolved conversation | `usage` on generations | the number that actually scales with users |

Alert on all five. Thresholds and the `az` commands are in
[`deploy/azure/README.md`](../deploy/azure/README.md#7-observability-in-production).

## Swapping vendors

Implement `Tracer` (`span`, `score`, `event`, `flush`) for OpenTelemetry, Arize
Phoenix, W&B Weave or LangSmith, and return it from `get_tracer`. No other
module imports a tracing SDK.
