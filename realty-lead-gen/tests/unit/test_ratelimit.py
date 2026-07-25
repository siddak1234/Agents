"""Unit tests for the rate-limit guard itself.

These run against `limits`' in-process memory backend, so they exercise the
real strategy code — the same ``MovingWindowRateLimiter`` production uses —
without needing Redis. The Redis-specific concern (fail-open when the store
is unreachable) is covered by pointing a guard at a closed port, which is a
faster and more deterministic way to produce a connection failure than
stopping a real server mid-test.
"""

from __future__ import annotations

import pytest

from realty_lead_gen.api.ratelimit import (
    HEADER_LIMIT,
    HEADER_REMAINING,
    HEADER_RESET,
    HEADER_RETRY_AFTER,
    RateLimitDecision,
    RateLimitGuard,
    build_rate_limiter,
    storage_uri_for,
)
from realty_lead_gen.config import Settings


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "app_env": "test",
        "jwt_hs_secret": "test-secret-not-a-secret",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.mark.unit
async def test_allows_up_to_the_limit_then_denies() -> None:
    guard = RateLimitGuard(per_minute=3, storage_uri="async+memory://")
    verdicts = [await guard.check("subject-a") for _ in range(5)]

    assert [v.allowed for v in verdicts if v] == [True, True, True, False, False]
    # `remaining` counts down and floors at zero rather than going negative,
    # because a negative value in an X-RateLimit-Remaining header is a lie
    # every client dashboard renders badly.
    assert [v.remaining for v in verdicts if v] == [2, 1, 0, 0, 0]
    assert all(v.limit == 3 for v in verdicts if v)


@pytest.mark.unit
async def test_subjects_do_not_share_a_bucket() -> None:
    """The whole point of keying on `sub`: one noisy tenant cannot starve another."""
    guard = RateLimitGuard(per_minute=2, storage_uri="async+memory://")
    for _ in range(2):
        await guard.check("subject-a")

    exhausted = await guard.check("subject-a")
    untouched = await guard.check("subject-b")

    assert exhausted is not None
    assert untouched is not None
    assert exhausted.allowed is False
    assert untouched.allowed is True
    assert untouched.remaining == 1


@pytest.mark.unit
async def test_denied_decision_never_says_retry_immediately() -> None:
    guard = RateLimitGuard(per_minute=1, storage_uri="async+memory://")
    await guard.check("subject-c")
    denied = await guard.check("subject-c")

    assert denied is not None
    assert denied.allowed is False
    # A moving window over one minute may compute a sub-second reset; rounding
    # that to 0 would tell the client to retry with no delay at all.
    assert denied.reset_after >= 1
    assert denied.headers()[HEADER_RETRY_AFTER] == str(denied.reset_after)


@pytest.mark.unit
async def test_fail_open_when_storage_unreachable() -> None:
    """Redis down must not take the API down.

    Port 6399 has nothing listening; `limits` wraps the driver's
    ConnectionError into StorageError, which the guard turns into "unmetered".
    """
    guard = RateLimitGuard(
        per_minute=1,
        storage_uri="async+redis://127.0.0.1:6399/0",
    )
    assert await guard.check("subject-d") is None
    # Still None on the second call — no cached state that would flip it to a
    # denial once the first "hit" failed.
    assert await guard.check("subject-d") is None


@pytest.mark.unit
def test_sync_storage_backend_is_rejected_at_construction() -> None:
    """A sync backend would block the event loop on every request.

    `storage_uri_for` already refuses to hand one over, but the guard is a
    public class and this is the check that makes the constraint structural.
    Failing here is loud; failing at runtime would just be a slow API nobody
    can explain.
    """
    with pytest.raises(TypeError, match="async\\+"):
        RateLimitGuard(per_minute=1, storage_uri="memory://")


@pytest.mark.unit
def test_allowed_decision_carries_no_retry_after() -> None:
    headers = RateLimitDecision(allowed=True, limit=120, remaining=119, reset_after=42).headers()

    assert headers == {
        HEADER_LIMIT: "120",
        HEADER_REMAINING: "119",
        HEADER_RESET: "42",
    }
    assert HEADER_RETRY_AFTER not in headers


@pytest.mark.unit
def test_storage_uri_derived_from_redis_url() -> None:
    assert storage_uri_for(_settings(redis_url="redis://cache:6379/3")) == (
        "async+redis://cache:6379/3"
    )
    assert storage_uri_for(_settings(redis_url="rediss://cache:6380/0")) == (
        "async+rediss://cache:6380/0"
    )


@pytest.mark.unit
def test_storage_uri_override_must_be_async() -> None:
    """A sync `limits` backend would block the event loop on every request."""
    with pytest.raises(ValueError, match="async\\+"):
        storage_uri_for(_settings(api_rate_limit_storage_uri="redis://cache:6379/0"))

    assert (
        storage_uri_for(_settings(api_rate_limit_storage_uri="async+memory://"))
        == "async+memory://"
    )


@pytest.mark.unit
def test_unusable_redis_url_is_a_startup_error_not_a_silent_fallback() -> None:
    with pytest.raises(ValueError, match="cannot derive"):
        storage_uri_for(_settings(redis_url="memcached://cache:11211"))


@pytest.mark.unit
def test_disabled_flag_yields_no_guard() -> None:
    assert build_rate_limiter(_settings(api_rate_limit_enabled=False)) is None

    guard = build_rate_limiter(
        _settings(
            api_rate_limit_enabled=True,
            api_rate_limit_per_minute=7,
            api_rate_limit_storage_uri="async+memory://",
        )
    )
    assert guard is not None
    assert guard.per_minute == 7


@pytest.mark.unit
def test_build_does_no_io() -> None:
    """Constructing the guard must not require Redis to be up.

    `main.py` builds the app at import time, so a guard that connected eagerly
    would make importing the module fail wherever Redis is absent — including
    in CI's unit-test job.
    """
    guard = build_rate_limiter(
        _settings(
            api_rate_limit_enabled=True,
            redis_url="redis://127.0.0.1:6399/0",
        )
    )
    assert guard is not None
    assert guard.storage_uri == "async+redis://127.0.0.1:6399/0"
