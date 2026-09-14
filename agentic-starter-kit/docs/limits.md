# Rate limits and quotas

Two controls that answer different questions. Conflating them is the usual bug.

| Control | Question | Checked | Configured by |
| --- | --- | --- | --- |
| **Rate limit** | how *fast*? | before the run | `RATE_LIMIT_RPM`, `RATE_LIMIT_BURST` |
| **Request quota** | how *many* today? | before the run | `QUOTA_REQUESTS_PER_DAY` |
| **Token quota** | how *much* today? | before the run, charged after | `QUOTA_TOKENS_PER_DAY` |

Set any of them to `0` to disable that control alone.

## Why tokens are charged after the run

You do not know a run's token usage until it finishes. The limiter therefore
checks the *already-recorded* total before starting and records actual usage
afterwards, so the run that crosses the line is allowed to complete and the
next one is refused. The alternative — killing a run mid-answer — bills the
user for a truncated response and makes the agent look broken.

## Identity

`identity_of(user_id, client_host)` prefers the authenticated user and falls
back to the client address. Anonymous callers share a bucket per address, which
is deliberate: an unauthenticated endpoint must not hand out per-caller quota to
anyone willing to omit a header. Put Entra ID auth in front (see the
[Azure guide](../deploy/azure/README.md#5-authentication)) and pass the subject
as `user_id`.

## Responses

A refused request gets `429` with `Retry-After` and `X-RateLimit-Exceeded`.
Allowed requests carry `X-RateLimit-Burst-Remaining` and, when quotas are on,
`X-RateLimit-Requests-Remaining` / `X-RateLimit-Tokens-Remaining`.

## The multi-replica caveat — read this before you rely on the numbers

`InMemoryQuotaStore` is per process. With N replicas the effective limit is
N× what you configured. That is acceptable for a burst guard and **wrong for
billing**. Implement `QuotaStore` (three methods: `incr`, `get`, `reset`)
against Redis or Cosmos DB before the numbers matter:

```python
class RedisQuotaStore:
    def incr(self, key, amount, window_seconds, now):
        slot = f"{key}:{int(now // window_seconds)}"
        value = self.redis.incrby(slot, amount)
        self.redis.expire(slot, int(window_seconds * 2))
        return value
    ...
```

For a public endpoint, put Azure API Management or Front Door in front as well:
this limiter protects the model budget, a gateway protects the service.

## Standalone use

The limiter is framework-free — the FastAPI layer is a thin adapter — so the
same object works in a worker, a CLI, or a test:

```python
from starter.ratelimit import RateLimiter, RateLimited

limits = RateLimiter(requests_per_minute=30, burst=5, tokens_per_day=200_000)
try:
    limits.enforce(identity)
except RateLimited as exc:
    return f"slow down, retry in {exc.retry_after:.0f}s"
state = agent.run(query)
limits.record_usage(identity, state["usage"].get("total_tokens", 0))
```

The clock is injectable (`now`, `wall`), which is why the test suite asserts
refill and window-reset behaviour without sleeping.
