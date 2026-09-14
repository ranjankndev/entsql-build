"""Rate limits and quotas. The clock is injected, so nothing here sleeps."""

import pytest

from starter.ratelimit import (
    InMemoryQuotaStore,
    RateLimited,
    RateLimiter,
    TokenBucket,
    build_limiter,
    identity_of,
)


def limiter(**kwargs) -> tuple[RateLimiter, list[float]]:
    clock = [0.0]
    limits = RateLimiter(
        store=InMemoryQuotaStore(),
        now=lambda: clock[0],
        wall=lambda: clock[0],
        **kwargs,
    )
    return limits, clock


# --------------------------------------------------------------- the bucket


def test_bucket_allows_a_burst_then_refills():
    bucket = TokenBucket(rate=1.0, burst=3)
    assert [bucket.take(0.0)[0] for _ in range(3)] == [True, True, True]
    allowed, wait = bucket.take(0.0)
    assert not allowed and wait == pytest.approx(1.0)
    assert bucket.take(1.0)[0] is True  # one second, one token


def test_bucket_never_exceeds_its_capacity():
    bucket = TokenBucket(rate=10.0, burst=2)
    bucket.take(0.0)
    bucket.take(1_000.0)  # long idle period
    assert bucket.tokens <= 2


# --------------------------------------------------------------- rate limit


def test_requests_per_minute_is_enforced_per_identity():
    limits, _clock = limiter(requests_per_minute=60, burst=2)
    assert limits.check("user:a").allowed
    assert limits.check("user:a").allowed
    denied = limits.check("user:a")
    assert not denied.allowed and denied.limit == "requests_per_minute"
    assert denied.retry_after >= 1.0
    assert limits.check("user:b").allowed  # a different caller is unaffected


def test_refill_restores_capacity():
    limits, clock = limiter(requests_per_minute=60, burst=1)
    assert limits.check("u").allowed
    assert not limits.check("u").allowed
    clock[0] = 1.0
    assert limits.check("u").allowed


def test_enforce_raises_with_retry_after():
    limits, _ = limiter(requests_per_minute=60, burst=1)
    limits.enforce("u")
    with pytest.raises(RateLimited) as excinfo:
        limits.enforce("u")
    assert excinfo.value.limit == "requests_per_minute"
    assert excinfo.value.retry_after >= 1.0


# ------------------------------------------------------------------ quotas


def test_daily_request_quota():
    limits, _clock = limiter(requests_per_minute=0, requests_per_day=2)
    assert limits.check("u").allowed
    assert limits.check("u").allowed
    denied = limits.check("u")
    assert not denied.allowed and denied.limit == "requests_per_day"
    assert denied.remaining_requests == 0


def test_daily_quota_resets_with_the_window():
    limits, clock = limiter(requests_per_minute=0, requests_per_day=1)
    assert limits.check("u").allowed
    assert not limits.check("u").allowed
    clock[0] = 86_400.0
    assert limits.check("u").allowed


def test_token_quota_is_charged_after_the_run():
    limits, _ = limiter(requests_per_minute=0, tokens_per_day=100)
    assert limits.check("u").allowed
    limits.record_usage("u", 150)  # the run that overshoots is allowed to finish
    denied = limits.check("u")
    assert not denied.allowed and denied.limit == "tokens_per_day"
    assert denied.remaining_tokens == 0


def test_remaining_counters_are_reported():
    limits, _ = limiter(requests_per_minute=60, burst=5, requests_per_day=10, tokens_per_day=1000)
    decision = limits.check("u")
    assert decision.remaining_requests == 9
    assert decision.remaining_tokens == 1000
    assert decision.remaining_burst == pytest.approx(4.0)


def test_a_denied_request_is_not_charged_to_the_daily_quota():
    limits, _ = limiter(requests_per_minute=60, burst=1, requests_per_day=10)
    limits.check("u")           # allowed, counted
    limits.check("u")           # denied by the bucket
    assert limits.store.get("req:u", limits.window_seconds, 0.0) == 1


def test_zero_disables_each_control():
    limits, _ = limiter(requests_per_minute=0, requests_per_day=0, tokens_per_day=0)
    for _ in range(50):
        assert limits.check("u").allowed


# ----------------------------------------------------------------- headers


def test_headers_on_allow_and_deny():
    limits, _ = limiter(requests_per_minute=60, burst=1, requests_per_day=5)
    allowed = limits.check("u").headers()
    assert "X-RateLimit-Requests-Remaining" in allowed
    assert "Retry-After" not in allowed

    denied = limits.check("u").headers()
    assert denied["Retry-After"]
    assert denied["X-RateLimit-Exceeded"] == "requests_per_minute"


# ---------------------------------------------------------------- identity


def test_identity_prefers_the_authenticated_user():
    assert identity_of("alice", "10.0.0.1") == "user:alice"
    assert identity_of(None, "10.0.0.1") == "ip:10.0.0.1"
    assert identity_of(None, None) == "ip:unknown"


def test_build_limiter_honours_the_kill_switch(settings):
    settings.rate_limit_enabled = False
    assert build_limiter(settings) is None
    settings.rate_limit_enabled = True
    assert isinstance(build_limiter(settings), RateLimiter)
