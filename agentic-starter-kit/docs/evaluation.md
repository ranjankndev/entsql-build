# Evaluation

An agent without an eval suite is a demo. The difference is whether you can
answer "did that prompt change make it better?" with evidence.

## The loop

```
incident or idea → add a case → run the suite → change the agent → re-run → gate in CI
```

## Building the dataset

Start with 15–25 cases covering:

| Category | Example | Scorers |
| --- | --- | --- |
| Happy path | the three questions you built this for | `contains_all`, `tool_use` |
| Retrieval grounding | an answer that must come from a tool | `tool_use`, `contains_all` |
| Unknowns | something the agent cannot know | `contains_all` with "don't have" |
| Efficiency | a question needing no tool at all | `budget`, `tool_errors` |
| Safety | injection, harmful request, PII | `refusal`, `guardrails` |
| False positives | benign text near a policy boundary | `refusal` (expecting no refusal) |
| Regressions | every bug you have fixed | whatever caught it |

Case format (`.jsonl`, one object per line):

```json
{"id": "core-kb-refund", "query": "How long for a refund?",
 "expect_contains": ["14 days"], "expect_tools": ["search_kb"],
 "max_iterations": 3, "scorers": ["contains_all", "tool_use", "budget"],
 "tags": ["core", "retrieval"]}
```

## Choosing scorers

Adopt in this order; only move down when the level above cannot express what
you need.

1. **Deterministic** — `exact_match`, `contains_all`, `excludes_all`,
   `refusal`, `tool_use`, `tool_errors`, `budget`, `guardrails`. Free, stable,
   and the only kind that belongs in a merge gate.
2. **Statistical** — `similarity` (token overlap). A cheap signal on open-ended
   answers; a threshold, not a verdict.
3. **LLM judge** — `llm_judge(llm, criterion, threshold)` for tone,
   helpfulness, faithfulness. Use a *different* model than the agent, pin its
   version, set temperature 0, and sample rather than grading every case on
   every commit. A judge that drifts silently is worse than no judge.

## Gating

```bash
starter eval --suite evals/datasets/smoke.jsonl  --threshold 1.0   # per PR
starter eval --suite evals/datasets/safety.jsonl --threshold 1.0   # per PR
starter eval --suite evals/datasets/core.jsonl   --threshold 0.85  # nightly
```

Safety gates at **1.0** — a safety regression is never an acceptable trade.
Quality gates below 1.0 and runs nightly, because model variance would
otherwise block unrelated merges. Both are wired in
`.github/workflows/ci.yml`.

## Reading a report

`report.to_markdown()` gives pass rate, per-scorer means, p95 latency and a
failure table. The JSON in `.eval-runs/` carries each case's `trace_id`, so a
failure in CI links straight to the Langfuse trace that produced it — scores
are pushed to that same trace id by `run_suite`.

Watch the per-scorer means over time, not just the pass rate: `tool_use`
sliding while `contains_all` holds means the agent is getting the right answer
for the wrong reason, and that breaks the day your data changes.

## Regression discipline

Every production bug becomes a case in the same commit as the fix. This is the
single highest-value habit in the whole kit; a suite grown from real incidents
beats any hand-written benchmark.
