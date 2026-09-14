"""Rate limits and per-user quotas.

Two different controls, deliberately separate because they answer different
questions:

* **Rate limit** — "how fast?" A token bucket per identity: smooth refill,
  configurable burst. Checked *before* the run, because the point is to not do
  the work.
* **Quota** — "how much in total?" Fixed windows for requests/day and
  tokens/day. Requests are counted before the run; tokens can only be counted
  after it, since that is when usage is known. A run that blows the token quota
  is allowed to finish and the *next* one is refused — the alternative is
  billing the user for a truncated answer.

Framework-free on purpose: the FastAPI layer is a thin adapter over this, and
the same limiter works in a worker, a CLI or a test.

**Multi-replica**: the in-memory store is per process, so N replicas allow N×
the limit. That is fine for a burst guard and wrong for billing. Point
`QuotaStore` at Redis or Cosmos DB before you rely on the numbers — the
protocol is three methods.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from starter.settings import Settings, get_settings


class RateLimited(Exception):
    """Raised when a caller is over a limit. Carries what the caller needs to
    retry: which limit, and when."""

    def __init__(self, limit: str, retry_after: float, detail: str = "") -> None:
        super().__init__(detail or f"{limit} exceeded, retry in {retry_after:.0f}s")
        self.limit = limit
        self.retry_after = max(1.0, retry_after)
        self.detail = detail or str(self)


@dataclass
class Decision:
    allowed: bool = True
    limit: str = ""
    retry_after: float = 0.0
    remaining_burst: float = 0.0
    remaining_requests: int | None = None
    remaining_tokens: int | None = None

    def headers(self) -> dict[str, str]:
        """Standard-ish response headers. Clients and gateways both read these."""
        out = {"X-RateLimit-Burst-Remaining": f"{self.remaining_burst:.2f}"}
        if self.remaining_requests is not None:
            out["X-RateLimit-Requests-Remaining"] = str(self.remaining_requests)
        if self.remaining_tokens is not None:
            out["X-RateLimit-Tokens-Remaining"] = str(self.remaining_tokens)
        if not self.allowed:
            out["Retry-After"] = str(int(self.retry_after))
            out["X-RateLimit-Exceeded"] = self.limit
        return out


@dataclass
class TokenBucket:
    """Classic token bucket: `rate` tokens/second, capacity `burst`."""

    rate: float
    burst: float
    tokens: float = field(init=False)
    # None until the first call: a clock that legitimately starts at 0.0 must
    # not be mistaken for "never used".
    updated: float | None = None

    def __post_init__(self) -> None:
        self.tokens = self.burst

    def take(self, now: float, amount: float = 1.0) -> tuple[bool, float]:
        """Returns (allowed, seconds until `amount` would be available)."""
        elapsed = 0.0 if self.updated is None else max(0.0, now - self.updated)
        self.updated = now
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        if self.tokens >= amount:
            self.tokens -= amount
            return True, 0.0
        deficit = amount - self.tokens
        return False, deficit / self.rate if self.rate > 0 else float("inf")


@runtime_checkable
class QuotaStore(Protocol):
    """Counter store. Swap for Redis/Cosmos to make limits hold across replicas."""

    def incr(self, key: str, amount: int, window_seconds: float, now: float) -> int: ...

    def get(self, key: str, window_seconds: float, now: float) -> int: ...

    def reset(self) -> None: ...


class InMemoryQuotaStore:
    """Fixed windows in process memory. Correct for one replica, only."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, int], int] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _window(window_seconds: float, now: float) -> int:
        return int(now // window_seconds)

    def incr(self, key: str, amount: int, window_seconds: float, now: float) -> int:
        slot = (key, self._window(window_seconds, now))
        with self._lock:
            self._counts[slot] = self._counts.get(slot, 0) + amount
            return self._counts[slot]

    def get(self, key: str, window_seconds: float, now: float) -> int:
        return self._counts.get((key, self._window(window_seconds, now)), 0)

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


@dataclass
class RateLimiter:
    """Per-identity token bucket plus daily request and token quotas.

    `requests_per_minute <= 0`, `requests_per_day <= 0` or `tokens_per_day <= 0`
    disables that control individually.
    """

    requests_per_minute: float = 60.0
    burst: float = 10.0
    requests_per_day: int = 0
    tokens_per_day: int = 0
    window_seconds: float = 86_400.0
    store: QuotaStore = field(default_factory=InMemoryQuotaStore)
    now: Any = time.monotonic
    wall: Any = time.time
    _buckets: dict[str, TokenBucket] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ------------------------------------------------------------- checking

    def check(self, identity: str, cost: float = 1.0) -> Decision:
        """Called before the run. Never raises; inspect `Decision.allowed`."""
        decision = Decision()
        wall = self.wall()

        if self.tokens_per_day > 0:
            used = self.store.get(f"tok:{identity}", self.window_seconds, wall)
            decision.remaining_tokens = max(0, self.tokens_per_day - used)
            if used >= self.tokens_per_day:
                return self._deny(decision, "tokens_per_day", self._window_reset_in(wall))

        if self.requests_per_day > 0:
            used = self.store.get(f"req:{identity}", self.window_seconds, wall)
            decision.remaining_requests = max(0, self.requests_per_day - used)
            if used >= self.requests_per_day:
                return self._deny(decision, "requests_per_day", self._window_reset_in(wall))

        if self.requests_per_minute > 0:
            bucket = self._bucket(identity)
            allowed, wait = bucket.take(self.now(), cost)
            decision.remaining_burst = bucket.tokens
            if not allowed:
                return self._deny(decision, "requests_per_minute", wait)

        if self.requests_per_day > 0:
            used = self.store.incr(f"req:{identity}", 1, self.window_seconds, wall)
            decision.remaining_requests = max(0, self.requests_per_day - used)
        return decision

    def enforce(self, identity: str, cost: float = 1.0) -> Decision:
        """`check`, but raises `RateLimited` when refused."""
        decision = self.check(identity, cost)
        if not decision.allowed:
            raise RateLimited(decision.limit, decision.retry_after)
        return decision

    def record_usage(self, identity: str, tokens: int) -> None:
        """Called after the run, when token usage is finally known."""
        if self.tokens_per_day > 0 and tokens > 0:
            self.store.incr(f"tok:{identity}", tokens, self.window_seconds, self.wall())

    # ---------------------------------------------------------------- inner

    def _bucket(self, identity: str) -> TokenBucket:
        with self._lock:
            if identity not in self._buckets:
                self._buckets[identity] = TokenBucket(
                    rate=self.requests_per_minute / 60.0, burst=self.burst
                )
            return self._buckets[identity]

    def _window_reset_in(self, wall: float) -> float:
        return self.window_seconds - (wall % self.window_seconds)

    @staticmethod
    def _deny(decision: Decision, limit: str, retry_after: float) -> Decision:
        decision.allowed = False
        decision.limit = limit
        decision.retry_after = max(1.0, retry_after)
        return decision


def identity_of(user_id: str | None, client_host: str | None) -> str:
    """Who is being limited.

    Prefer the authenticated user; fall back to the client address. Anonymous
    callers therefore share a bucket per address, which is the conservative
    choice — an unauthenticated endpoint should not hand out per-caller quota
    to anyone who omits a header.
    """
    if user_id:
        return f"user:{user_id}"
    return f"ip:{client_host or 'unknown'}"


def build_limiter(settings: Settings | None = None) -> RateLimiter | None:
    s = settings or get_settings()
    if not s.rate_limit_enabled:
        return None
    return RateLimiter(
        requests_per_minute=s.rate_limit_rpm,
        burst=s.rate_limit_burst,
        requests_per_day=s.quota_requests_per_day,
        tokens_per_day=s.quota_tokens_per_day,
    )
