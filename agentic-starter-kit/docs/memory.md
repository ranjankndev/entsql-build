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

Retrieval is lexical (Jaccard overlap) out of the box — no infrastructure, and
unit-testable. To move to vectors, implement `search_records` against Azure AI
Search, pgvector or the Cosmos DB vector index. Nothing above the store
changes.

## Retention

The Bicep template sets a 30-day TTL on conversation documents. Long-term
records should set `ttl: -1` explicitly if they must outlive that. Agent
transcripts are personal data in most jurisdictions — match the TTL to your
privacy notice and make deletion-on-request reachable (`store.clear(thread_id)`).
