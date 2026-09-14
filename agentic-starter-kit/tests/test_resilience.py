"""Retry, backoff and circuit-breaker behaviour.

Sleeps are injected, so these assert the exact backoff curve in microseconds.
"""

import random

import pytest

from starter.llm.provider import LLMResponse
from starter.llm.resilience import (
    CircuitBreaker,
    LLMUnavailable,
    ResilientProvider,
    RetryPolicy,
    is_transient,
    retry_after_of,
)


class RateLimitError(Exception):
    def __init__(self, retry_after: float | None = None, status_code: int = 429):
        super().__init__("rate limited")
        self.status_code = status_code
        self.retry_after = retry_after


class BadRequestError(Exception):
    status_code = 400


class FlakyProvider:
    """Fails `failures` times, then answers."""

    name = "flaky"

    def __init__(self, failures: int, exc: Exception | None = None):
        self.failures = failures
        self.calls = 0
        self._exc = exc or RateLimitError()

    def complete(self, messages, tools=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise self._exc
        return LLMResponse(text="ok")

    def stream(self, messages, tools=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise self._exc
        yield "ok"
        yield LLMResponse(text="ok")


def build(inner, **kwargs):
    slept: list[float] = []
    provider = ResilientProvider(
        inner=inner,
        policy=kwargs.pop("policy", RetryPolicy(max_attempts=3, base_delay=1.0, jitter=0.0)),
        breaker=kwargs.pop("breaker", CircuitBreaker(failure_threshold=5)),
        sleep=slept.append,
        rng=random.Random(0),
    )
    return provider, slept


# ----------------------------------------------------------------- classify


def test_classifies_status_codes():
    assert is_transient(RateLimitError())
    assert not is_transient(BadRequestError())


def test_classifies_by_exception_name():
    class APIConnectionError(Exception):
        pass

    assert is_transient(APIConnectionError())


def test_reads_retry_after_from_header():
    class WithHeaders(Exception):
        status_code = 429
        headers = {"retry-after": "7"}

    assert retry_after_of(WithHeaders()) == 7.0


# -------------------------------------------------------------------- retry


def test_retries_transient_then_succeeds():
    provider, slept = build(FlakyProvider(failures=2))
    assert provider.complete([]).text == "ok"
    assert provider.inner.calls == 3
    assert slept == [1.0, 2.0]  # exponential, jitter disabled


def test_gives_up_after_max_attempts():
    provider, slept = build(FlakyProvider(failures=99))
    with pytest.raises(RateLimitError):
        provider.complete([])
    assert provider.inner.calls == 3
    assert len(slept) == 2  # no sleep after the final attempt


def test_permanent_errors_are_not_retried():
    provider, slept = build(FlakyProvider(failures=99, exc=BadRequestError()))
    with pytest.raises(BadRequestError):
        provider.complete([])
    assert provider.inner.calls == 1
    assert slept == []


def test_retry_after_header_wins_over_backoff():
    provider, slept = build(FlakyProvider(failures=1, exc=RateLimitError(retry_after=9)))
    provider.complete([])
    assert slept == [9.0]


def test_retry_after_is_capped_by_max_delay():
    policy = RetryPolicy(max_attempts=2, base_delay=1.0, max_delay=5.0, jitter=0.0)
    provider, slept = build(FlakyProvider(failures=1, exc=RateLimitError(retry_after=600)), policy=policy)
    provider.complete([])
    assert slept == [5.0]


def test_jitter_only_reduces_the_delay():
    policy = RetryPolicy(max_attempts=3, base_delay=4.0, jitter=0.5)
    provider, slept = build(FlakyProvider(failures=2), policy=policy)
    provider.complete([])
    assert all(2.0 <= d <= 4.0 for d in slept[:1])
    assert all(4.0 <= d <= 8.0 for d in slept[1:])


# ---------------------------------------------------------------- breaker


def test_breaker_opens_and_fails_fast():
    clock = [0.0]
    breaker = CircuitBreaker(failure_threshold=2, reset_seconds=30, now=lambda: clock[0])
    provider, _ = build(FlakyProvider(failures=99), breaker=breaker)

    with pytest.raises(RateLimitError):
        provider.complete([])
    assert breaker.state == "open"

    calls_before = provider.inner.calls
    with pytest.raises(LLMUnavailable):
        provider.complete([])
    assert provider.inner.calls == calls_before  # nothing reached the provider


def test_breaker_half_opens_then_closes_on_success():
    clock = [0.0]
    breaker = CircuitBreaker(failure_threshold=1, reset_seconds=30, now=lambda: clock[0])
    inner = FlakyProvider(failures=1)
    provider, _ = build(inner, breaker=breaker)

    with pytest.raises(RateLimitError):
        provider.complete([])
    assert breaker.state == "open"

    clock[0] = 31.0
    assert breaker.state == "half_open"
    assert provider.complete([]).text == "ok"
    assert breaker.state == "closed" and breaker.failures == 0


def test_success_resets_the_failure_count():
    breaker = CircuitBreaker(failure_threshold=3)
    provider, _ = build(FlakyProvider(failures=1), breaker=breaker)
    provider.complete([])
    assert breaker.failures == 0


# --------------------------------------------------------------- streaming


def test_stream_retries_before_the_first_delta():
    provider, slept = build(FlakyProvider(failures=1))
    chunks = list(provider.stream([]))
    assert chunks[0] == "ok"
    assert slept == [1.0]


def test_stream_falls_back_to_complete_without_provider_support():
    class NoStream:
        name = "nostream"

        def complete(self, messages, tools=None):
            return LLMResponse(text="only complete")

    provider, _ = build(NoStream())
    chunks = list(provider.stream([]))
    assert len(chunks) == 1 and chunks[0].text == "only complete"


# ------------------------------------------------------------------ wiring


def test_wrap_resilient_respects_the_kill_switch(settings):
    from starter.llm.resilience import wrap_resilient

    inner = FlakyProvider(failures=0)
    settings.llm_retry_enabled = False
    assert wrap_resilient(inner, settings) is inner
    settings.llm_retry_enabled = True
    assert isinstance(wrap_resilient(inner, settings), ResilientProvider)


def test_agent_reports_an_open_circuit_without_crashing(agent):
    class Dead:
        name = "dead"

        def complete(self, messages, tools=None):
            raise LLMUnavailable("circuit open")

    agent.deps.llm = Dead()
    state = agent.run("hello")
    assert state["stop_reason"] == "error"
    assert "circuit open" in state["error"]
