# Latency budget and load testing

## Measure your own overhead first

Almost everyone load-tests the deployed endpoint, sees "p95 = 4 s", and starts
tuning prompts. The useful first measurement is the one with the model taken
out:

```bash
LLM_PROVIDER=echo starter loadtest --requests 200 --concurrency 10
```

That is your code — guardrails, memory assembly, graph bookkeeping — with no
model, no network and no serialisation. On this kit's defaults it lands around
**9 ms p50 / 12 ms p95** per turn. If yours is 300 ms, no amount of model
tuning will save you, and you would never have learned that from the endpoint.

Then measure the deployed service:

```bash
starter loadtest --url https://<app>/chat --requests 200 --concurrency 10
```

The difference between the two runs is the infrastructure's contribution,
itemised rather than guessed.

## A budget worth defending

For a single-turn agent with one tool call, on a warm replica:

| Stage | Budget (p95) | Blows up when |
| --- | --- | --- |
| Input guardrails | < 5 ms | a model-based guard runs at `sample_rate: 1.0` — sample it, or move it to the output stage |
| Memory assembly | < 30 ms | long-term retrieval is unbounded, or summarisation runs on the request path |
| Model call (first) | 300–1500 ms | prompt is bloated, or you are on a shared-quota deployment — check for 429s first |
| Tool call | < 200 ms | a downstream API with no timeout; every tool needs one |
| Model call (second) | 300–1500 ms | two round trips is the cost of tool use — cache the tool result if the query repeats |
| Output guardrails | < 5 ms, or 300–800 ms with a judge | a groundedness judge doubles your model calls; sample it |
| **Total** | **≈ 1–3 s** | more than two tool round trips — that is a design problem, not a tuning one |

The single biggest lever is the **number of model round trips**, not the speed
of any one of them. `AGENT_MAX_ITERATIONS` bounds the worst case; the
`iteration` count in every trace tells you the typical one. If p95 iterations is
3 when it should be 1, fix the prompt or the tool descriptions — that is worth
more than any infrastructure change.

## Reading a load test

```
- requests: 200 at concurrency 10
- throughput: 8.4 req/s over 23.8s
- error rate: 0.0%
| p50 | 980 |   | p95 | 2400 |   | p99 | 4100 |
```

| Pattern | What it means | What to do |
| --- | --- | --- |
| p50 flat, p99 far out | queuing behind too few replicas, or a cold start | raise `minReplicas`, lower the Container Apps `concurrentRequests` rule |
| everything rises with concurrency | you are model-throughput-bound | more PTU/capacity, or a smaller model for the easy path |
| errors climb at higher concurrency | 429s from the provider, or your own rate limiter | check the `stop_reason` and status breakdown before blaming the model |
| `stop_reason: max_iterations` appears | the agent thrashes under load *because* of timeouts mid-loop | fix the loop, not the replicas |

Sample-size honesty: percentiles need roughly 100× the tail you want to quote.
200 requests support a p95; they do not support a p99, and the report says so
rather than printing a confident number.

## Concurrency and replicas

The Container Apps scale rule in `deploy/azure/bicep/main.bicep` uses
`concurrentRequests: 20`. Match it to what one replica actually sustains at
your p95 target — measure, do not assume:

```bash
for c in 5 10 20 40; do
  starter loadtest --url https://<app>/chat --requests 200 --concurrency $c --json \
    | python -c "import json,sys; d=json.load(sys.stdin); print(c, d['latency_ms']['p95'], d['throughput_rps'])"
done
```

The right value is the concurrency just below where p95 starts climbing. Set it
too high and requests queue inside a replica where no autoscaler can see them;
too low and you pay for idle replicas.

## Gating on latency

```bash
starter loadtest --requests 200 --concurrency 10 --max-p95 3000
```

Exits non-zero when the budget is blown, so it can sit in a deploy pipeline next
to the eval gates. Run it against staging, not production, and run it after the
safety suite — a fast agent that fails its safety evals is not a candidate for
release.
