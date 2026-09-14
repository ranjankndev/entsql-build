"""Retry, backoff and circuit breaking for the LLM seam.

Model endpoints fail in three distinct ways and they want different answers:

* **Transient** (429, 500, 503, connection reset, timeout) — retry with
  exponential backoff plus jitter, and honour `Retry-After` when the provider
  sends one; it knows more than your backoff curve does.
* **Persistent** (the endpoint is down, quota exhausted) — stop hammering it.
  The circuit breaker opens after `failure_threshold` consecutive transient
  failures and fails fast for `reset_seconds`, then lets one probe through.
* **Permanent** (bad request, auth, content filter) — never retried. Retrying a
  400 just burns latency and hides the bug.

`sleep` is injectable, so the tests assert the exact backoff curve without
waiting for it.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from starter.llm.provider import LLMProvider, LLMResponse
from starter.settings import Settings, get_settings

log = logging.getLogger("starter.llm")

# Matched against the exception's class name, lowercased. Vendor SDKs disagree
# on types but agree on names, and this keeps the kit free of vendor imports.
TRANSIENT_NAMES: tuple[str, ...] = (
    "ratelimit",
    "timeout",
    "serviceunavailable",
    "internalserver",
    "apiconnection",
    "connectionerror",
    "overloaded",
    "toomanyrequests",
)
TRANSIENT_STATUS: frozenset[int] = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class LLMUnavailable(RuntimeError):
    """The circuit is open: the provider is failing and we are not asking again
    until the reset window elapses."""


def status_code_of(exc: BaseException) -> int | None:
    for attr in ("status_code", "http_status", "code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def retry_after_of(exc: BaseException) -> float | None:
    """Seconds the provider asked us to wait, from the header or the exception."""
    headers = getattr(getattr(exc, "response", None), "headers", None) or getattr(
        exc, "headers", None
    )
    raw: Any = None
    if isinstance(headers, dict):
        raw = headers.get("retry-after") or headers.get("Retry-After")
    elif headers is not None and hasattr(headers, "get"):
        raw = headers.get("retry-after")
    if raw is None:
        raw = getattr(exc, "retry_after", None)
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def is_transient(exc: BaseException) -> bool:
    status = status_code_of(exc)
    if status is not None:
        return status in TRANSIENT_STATUS
    name = type(exc).__name__.lower()
    return any(marker in name for marker in TRANSIENT_NAMES)


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 20.0
    jitter: float = 0.25  # fraction of the delay, applied as full jitter
    respect_retry_after: bool = True

    def delay_for(self, attempt: int, exc: BaseException, rng: random.Random) -> float:
        """Attempt is 1-based: the delay *after* the attempt-th failure."""
        if self.respect_retry_after:
            asked = retry_after_of(exc)
            if asked is not None:
                return min(asked, self.max_delay)
        delay = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        return delay * (1 - self.jitter * rng.random())


@dataclass
class CircuitBreaker:
    """Closed → (failures) → Open → (reset_seconds) → Half-open → Closed/Open."""

    failure_threshold: int = 5
    reset_seconds: float = 30.0
    now: Any = time.monotonic
    failures: int = 0
    opened_at: float | None = None

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if self.now() - self.opened_at >= self.reset_seconds else "open"

    def before_call(self) -> None:
        if self.state == "open":
            raise LLMUnavailable(
                f"circuit open after {self.failures} consecutive failures; "
                f"retrying in {self.reset_seconds - (self.now() - (self.opened_at or 0)):.1f}s"
            )

    def on_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def on_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = self.now()


@dataclass
class ResilientProvider:
    """Wraps any `LLMProvider` with retries and a breaker.

    Streaming is retried only *before the first delta reaches the caller* —
    once text has left, re-running the call would duplicate it. That boundary
    is deliberate and tested.
    """

    inner: LLMProvider
    policy: RetryPolicy = field(default_factory=RetryPolicy)
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    sleep: Any = time.sleep
    rng: random.Random = field(default_factory=lambda: random.Random(0))
    attempts_made: int = 0

    @property
    def name(self) -> str:
        return f"{self.inner.name}+resilient"

    def complete(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return self._call(lambda: self.inner.complete(messages, tools))

    def stream(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[str | LLMResponse]:
        stream = getattr(self.inner, "stream", None)
        if not callable(stream):
            yield self.complete(messages, tools)
            return

        def first_chunk_and_rest() -> tuple[Any, Iterator[Any]]:
            iterator = iter(stream(messages, tools))
            return next(iterator, None), iterator

        first, rest = self._call(first_chunk_and_rest)
        if first is None:
            return
        yield first
        yield from rest

    # ---------------------------------------------------------------- inner

    def _call(self, fn: Any) -> Any:
        self.breaker.before_call()
        last: BaseException | None = None
        for attempt in range(1, self.policy.max_attempts + 1):
            self.attempts_made += 1
            try:
                result = fn()
            except Exception as exc:
                last = exc
                if not is_transient(exc):
                    self.breaker.on_failure()
                    raise
                self.breaker.on_failure()
                if attempt == self.policy.max_attempts or self.breaker.state != "closed":
                    break
                delay = self.policy.delay_for(attempt, exc, self.rng)
                log.warning(
                    "transient LLM failure (%s), attempt %d/%d, sleeping %.2fs",
                    type(exc).__name__,
                    attempt,
                    self.policy.max_attempts,
                    delay,
                )
                self.sleep(delay)
            else:
                self.breaker.on_success()
                return result
        assert last is not None
        raise last


def wrap_resilient(provider: LLMProvider, settings: Settings | None = None) -> LLMProvider:
    """Apply the configured retry/breaker policy. `LLM_RETRY_ENABLED=false`
    returns the provider untouched, which is what the eval harness wants when
    it is measuring raw provider behaviour."""
    s = settings or get_settings()
    if not s.llm_retry_enabled:
        return provider
    return ResilientProvider(
        inner=provider,
        policy=RetryPolicy(
            max_attempts=s.llm_max_attempts,
            base_delay=s.llm_retry_base_delay,
            max_delay=s.llm_retry_max_delay,
        ),
        breaker=CircuitBreaker(
            failure_threshold=s.llm_breaker_threshold,
            reset_seconds=s.llm_breaker_reset_seconds,
        ),
    )
