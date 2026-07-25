"""The rate limiter as actually mounted on the real application.

The unit tests prove the guard counts correctly. These prove the wiring: that
it runs on the metered routers and not on the health probes, that it runs
*after* token verification so it can key on the subject, that a successful
response carries the quota headers, and that a breach comes back as an RFC
7807 problem document rather than slowapi's ``{"error": ...}``.

The app is built with an in-process ``async+memory://`` store so the test does
not need Redis; the strategy and decision code exercised are identical.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from realty_lead_gen.api.deps import get_session
from realty_lead_gen.config import get_settings
from realty_lead_gen.main import create_app
from realty_lead_gen.models.user import User, UserRole

if TYPE_CHECKING:
    from fastapi import FastAPI

PER_MINUTE = 2
EXTERNAL_ID = "ext-ratelimit-subject"


def _token(settings: Any, subject: str = EXTERNAL_ID) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": subject,
            "email": "dak@example.com",
            "role": "realtor",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        settings.jwt_hs_secret.get_secret_value(),
        algorithm="HS256",
    )


@pytest.fixture()
def _app(_integration_session) -> Any:
    """The real app, metered at PER_MINUTE, talking to the rolled-back session."""
    get_settings.cache_clear()
    settings = get_settings().model_copy(
        update={
            "api_rate_limit_enabled": True,
            "api_rate_limit_per_minute": PER_MINUTE,
            "api_rate_limit_storage_uri": "async+memory://",
        }
    )
    app: FastAPI = create_app(settings)
    # The routes must see the same transaction the test seeded, and must not
    # open a second connection that would deadlock against it.
    app.dependency_overrides[get_session] = lambda: _integration_session
    return app


def _client(app: Any) -> AsyncClient:
    # ASGITransport drives the app in-process and deliberately does not run
    # lifespan, so no engine pool is warmed and no Redis is dialled.
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.integration
async def test_quota_headers_then_429_problem_document(_app, _integration_session) -> None:
    session = _integration_session
    session.add(
        User(
            external_id=EXTERNAL_ID,
            email="dak@example.com",
            display_name="Dak",
            role=UserRole.realtor,
        )
    )
    await session.flush()

    headers = {"Authorization": f"Bearer {_token(_app.state.settings)}"}
    async with _client(_app) as client:
        first = await client.get("/leads", headers=headers)
        second = await client.get("/leads", headers=headers)
        third = await client.get("/leads", headers=headers)

    assert first.status_code == 200, first.text
    assert first.headers["x-ratelimit-limit"] == str(PER_MINUTE)
    assert first.headers["x-ratelimit-remaining"] == "1"
    assert "x-ratelimit-reset" in first.headers
    assert "retry-after" not in first.headers

    assert second.status_code == 200
    assert second.headers["x-ratelimit-remaining"] == "0"

    assert third.status_code == 429
    assert third.headers["content-type"].startswith("application/problem+json")
    body = third.json()
    assert body["type"].endswith("/errors/rate_limit_exceeded")
    assert body["status"] == 429
    assert body["limit"] == PER_MINUTE
    assert body["remaining"] == 0
    assert body["reset_after_seconds"] >= 1
    # RequestContextMiddleware stamps a request id; the problem document must
    # carry it so a 429 in a client log can be traced to a server log line.
    assert body["instance"] == third.headers["x-request-id"]
    assert int(third.headers["retry-after"]) == body["reset_after_seconds"]


@pytest.mark.integration
async def test_health_probes_are_never_metered(_app) -> None:
    async with _client(_app) as client:
        responses = [await client.get("/healthz") for _ in range(PER_MINUTE + 3)]

    assert [r.status_code for r in responses] == [200] * (PER_MINUTE + 3)
    assert all("x-ratelimit-limit" not in r.headers for r in responses)


@pytest.mark.integration
async def test_unauthenticated_request_is_401_not_429(_app) -> None:
    """Quota is spent by identified callers only.

    A request with no token never reaches the guard — `verify_token` rejects
    it first — so an anonymous flood cannot exhaust a real user's budget. It
    also means anonymous traffic is unthrottled here by design; that belongs
    at the edge, not in application code.
    """
    async with _client(_app) as client:
        responses = [await client.get("/leads") for _ in range(PER_MINUTE + 2)]

    assert [r.status_code for r in responses] == [401] * (PER_MINUTE + 2)
    assert all("x-ratelimit-limit" not in r.headers for r in responses)


@pytest.mark.integration
async def test_two_subjects_have_independent_budgets(_app, _integration_session) -> None:
    session = _integration_session
    other = "ext-ratelimit-other"
    for ext in (EXTERNAL_ID, other):
        session.add(
            User(
                external_id=ext,
                email=f"{ext}@example.com",
                display_name=ext,
                role=UserRole.realtor,
            )
        )
    await session.flush()

    settings = _app.state.settings
    mine = {"Authorization": f"Bearer {_token(settings)}"}
    theirs = {"Authorization": f"Bearer {_token(settings, other)}"}

    async with _client(_app) as client:
        for _ in range(PER_MINUTE):
            await client.get("/leads", headers=mine)
        exhausted = await client.get("/leads", headers=mine)
        untouched = await client.get("/leads", headers=theirs)

    assert exhausted.status_code == 429
    assert untouched.status_code == 200
    assert untouched.headers["x-ratelimit-remaining"] == str(PER_MINUTE - 1)


@pytest.mark.integration
async def test_disabled_flag_removes_all_metering(_integration_session) -> None:
    get_settings.cache_clear()
    settings = get_settings().model_copy(
        update={"api_rate_limit_enabled": False, "api_rate_limit_per_minute": 1}
    )
    app = create_app(settings)
    app.dependency_overrides[get_session] = lambda: _integration_session

    _integration_session.add(
        User(
            external_id=EXTERNAL_ID,
            email="dak@example.com",
            display_name="Dak",
            role=UserRole.realtor,
        )
    )
    await _integration_session.flush()

    headers = {"Authorization": f"Bearer {_token(settings)}"}
    async with _client(app) as client:
        responses = [await client.get("/leads", headers=headers) for _ in range(4)]

    assert app.state.rate_limiter is None
    assert [r.status_code for r in responses] == [200] * 4
    assert all("x-ratelimit-limit" not in r.headers for r in responses)
