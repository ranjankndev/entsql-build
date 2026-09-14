# Context memory

## The three layers

| Layer | Lives in | Lifetime | Built from |
| --- | --- | --- | --- |
| **Working** | `AgentState["messages"]` | one run | assembled fresh each turn |
| **Short term** | `MemoryStore` turns + rolling summary | one thread | every turn, capped at `SHORT_TERM_MAX_TURNS` |
| **Long term** | `MemoryStore` records | across threads, per user | facts you choose to write |

## Assembly order

`ContextMemory.build_context` always produces, oldest to newest:

1. the system prompt
2. long-term records retrieved for **this** query (capped at `max_records`)
3. the rolling summary of everything older than the window
4. the last `max_turns` turns verbatim
5. the current user message

Fixed order matters: it keeps the prompt diffable, makes token growth
predictable, and means a context bug is reproducible.

## Summarisation

When turns exceed `max(summary_trigger, max_turns)`, the overflow is folded
into the summary and dropped from the verbatim window. With an LLM configured
that is a real summarisation call; without one, a deterministic truncation —
so tests and CI never depend on a model.

Tune `SUMMARY_TRIGGER_TURNS` down if you see cost creep, up if the agent starts
forgetting things users said six turns ago.

## What to write to long-term memory

Write **decisions and stable facts**, not transcript. Good: "prefers metric
units", "account tier: enterprise", "escalation opened INC-4821". Bad: every
user message. Use a stable `key` so a repeat write is an upsert:

```python
memory.remember_fact(user_id, "Prefers metric units.", kind="preference", key="units")
```

Uncontrolled long-term memory is a slow-motion prompt-injection vector: text
the agent wrote once gets replayed as trusted context forever. Everything you
store should either come from a tool result you trust or a rule you wrote.

## Backends

| Backend | Setting | Use |
| --- | --- | --- |
| `in_memory` | default | tests, evals, local dev |
| `file` | `MEMORY_BACKEND=file` | single-process demo that survives restarts |
| `cosmos` | `MEMORY_BACKEND=cosmos` | production; partitioned by thread/owner, managed-identity auth, TTL on turns |

## Retrieval: lexical, vector, hybrid

| `MEMORY_BACKEND` | `search_records` uses | Infrastructure |
| --- | --- | --- |
| `in_memory` / `file` / `cosmos` | Jaccard token overlap | none |
| `vector` | hybrid: `alpha * cosine + (1 - alpha) * lexical` | none (in-process) |
| `vector` + `SEARCH_ENDPOINT` | Azure AI Search hybrid query, server-side | an AI Search index |

`VectorMemoryStore` **decorates** any store: turns and summaries pass straight
through, only `search_records` changes. That is the entire RAG upgrade path —
nothing above the store knows it happened.

### Why hybrid, not pure vector

Pure vector search misses the exact tokens users are most likely to quote —
order ids, SKUs, error codes. Pure lexical search misses paraphrase.
`HYBRID_ALPHA` weights the two; 0.7 is a sensible start, and `alpha=0`/`alpha=1`
degenerate cleanly to lexical-only and vector-only if you want to measure each.

### The embedder is a seam too

`EMBEDDING_PROVIDER=hashing` is the default: deterministic, offline, no
dependency, stable across processes (blake2b, not Python's salted `hash`) so a
vector cached last week still compares correctly today. **It is a bag-of-words
projection, not real semantics** — it exercises and tests the vector path, it
does not deliver semantic recall. Switch to `openai` or `azure_openai` before
claiming that.

Records are embedded once at write time, so a query costs one embedding call.

### Azure AI Search

`AzureAISearchStore` keeps turns and summaries in the inner store (Cosmos DB in
production — a search index is the wrong shape for an append-only transcript)
and puts long-term records in an index with fields `id` (key), `owner_id`
(filterable), `text`, `kind`, `ts` and `embedding`
(`Collection(Edm.Single)`, vector-searchable). Auth is
`DefaultAzureCredential`; grant the app's managed identity **Search Index Data
Contributor**.

To use pgvector or the Cosmos DB vector index instead, implement
`search_records` the same way. Nothing above the store changes.

## Retention

The Bicep template sets a 30-day TTL on conversation documents. Long-term
records should set `ttl: -1` explicitly if they must outlive that. Agent
transcripts are personal data in most jurisdictions — match the TTL to your
privacy notice and make deletion-on-request reachable (`store.clear(thread_id)`).
