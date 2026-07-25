"""`GET /leads` over HTTP, against real Postgres.

This is the endpoint the frontend is built on, and until now nothing
asserted what it *returns*. `test_rate_limit_api.py` calls it repeatedly but
only ever reads the status code, so every filter, the ownership predicate,
and the keyset cursor were shipped unverified end to end. Three of those are
the kind of defect that does not announce itself:

* a dropped ``Lead.user_id == user.id`` leaks one realtor's pipeline to
  another and still returns 200,
* a cursor whose comparison disagrees with its ``ORDER BY`` silently skips or
  repeats rows in the middle of a scroll, which reads as "the API is flaky",
* a filter that ORs where it should AND returns *more* results, which nobody
  reports as a bug.

Three deliberate choices about how this file is written:

* **The app is built with metering off.** `api_rate_limit_enabled=False`
  makes `build_rate_limiter` return `None`, so no quota store is dialled and
  a test that walks eleven pages cannot fail as a 429. What the limiter does
  when it *is* on is `test_rate_limit_api.py`'s subject, not this one's.
* **Pagination is asserted by walking, not by spot-checking a page.**
  `_walk` follows `next_cursor` to exhaustion and the assertion is over the
  concatenated result: every seeded lead exactly once, in one order. An
  off-by-one in the ``limit + 1`` probe or a ``<=`` where the cursor wants
  ``<`` shows up as a duplicate or a gap, which a single-page assertion
  cannot see.
* **Expected orderings are computed in Python** (``sorted(..., reverse=True)``
  over ``uuid.UUID``), not read back from the database. That makes
  `test_equal_scores_break_ties_by_id_without_gaps_or_repeats` an assertion
  that Postgres's ``uuid`` collation agrees with Python's — which the cursor
  depends on, since ``Lead.id < decoded.id`` is evaluated by Postgres against
  a UUID the API serialized and the client sent back as text.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from realty_lead_gen.api.deps import get_session
from realty_lead_gen.api.pagination import LeadCursor
from realty_lead_gen.config import get_settings
from realty_lead_gen.main import create_app
from realty_lead_gen.models.deal import DealAnalysis
from realty_lead_gen.models.lead import Lead, LeadStatus
from realty_lead_gen.models.property import Property, PropertyType
from realty_lead_gen.models.score import Persona
from realty_lead_gen.models.user import User, UserRole

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

OWNER = "ext-leads-owner"
STRANGER = "ext-leads-stranger"

#: Enough iterations to exhaust any page walk in this module several times
#: over. Its purpose is to turn a cursor that fails to advance into a failed
#: assertion instead of a hung test.
_MAX_PAGES = 20


# --- fixtures and seeding ----------------------------------------------------


def _token(settings: Any, subject: str = OWNER) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": subject,
            "email": f"{subject}@example.com",
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
def _app(_integration_session: AsyncSession) -> FastAPI:
    get_settings.cache_clear()
    settings = get_settings().model_copy(update={"api_rate_limit_enabled": False})
    app = create_app(settings)
    # The routes must read the transaction this test seeded, and must not
    # open a second connection that would block against it.
    app.dependency_overrides[get_session] = lambda: _integration_session
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _headers(app: FastAPI, subject: str = OWNER) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(app.state.settings, subject)}"}


async def _seed_user(session: AsyncSession, external_id: str) -> User:
    user = User(
        external_id=external_id,
        email=f"{external_id}@example.com",
        display_name=external_id,
        role=UserRole.realtor,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_property(
    session: AsyncSession,
    *,
    tag: str,
    city: str = "AUSTIN",
    postal_code: str = "78701",
) -> Property:
    prop = Property(
        # `address_hash` is unique and 64 chars; hashing the tag gives both
        # properties for free and keeps the fixture readable at the call site.
        address_hash=hashlib.sha256(tag.encode()).hexdigest(),
        street_number="123",
        street_name="MAIN ST",
        city=city,
        state="TX",
        postal_code=postal_code,
        property_type=PropertyType.single_family,
        attributes={},
    )
    session.add(prop)
    await session.flush()
    return prop


async def _seed_lead(
    session: AsyncSession,
    *,
    user: User,
    tag: str,
    city: str = "AUSTIN",
    postal_code: str = "78701",
    persona: Persona = Persona.flipper,
    score: str = "0.5000",
) -> Lead:
    """One lead on its own property.

    A fresh property per lead is not incidental: `lead` carries a unique
    index on (property_id, user_id, persona), so two leads for one user on
    one property is a state the database refuses to represent.
    """
    prop = await _seed_property(session, tag=tag, city=city, postal_code=postal_code)
    lead = Lead(
        property_id=prop.id,
        user_id=user.id,
        persona=persona,
        score_snapshot=Decimal(score),
        status=LeadStatus.new,
        surfaced_at=datetime.now(UTC),
    )
    session.add(lead)
    await session.flush()
    return lead


def _ids(payload: dict[str, Any]) -> list[str]:
    return [item["id"] for item in payload["items"]]


async def _walk(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    limit: int,
    **params: Any,
) -> list[str]:
    """Follow `next_cursor` to exhaustion, returning every id in page order."""
    collected: list[str] = []
    cursor: str | None = None
    for _ in range(_MAX_PAGES):
        query: dict[str, Any] = {"limit": limit, **params}
        if cursor is not None:
            query["cursor"] = cursor
        response = await client.get("/leads", headers=headers, params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) <= limit
        collected.extend(_ids(body))
        cursor = body["next_cursor"]
        if cursor is None:
            return collected
    raise AssertionError("cursor never terminated")


# --- ownership ---------------------------------------------------------------


async def test_a_caller_sees_only_their_own_leads(
    _app: FastAPI, _integration_session: AsyncSession
) -> None:
    """The one failure in this file that is a data breach rather than a bug.

    Both leads are identical in every respect the filters can see - same
    persona, same city, same zip, same score - so nothing but `user_id`
    separates them, and a dropped ownership predicate cannot hide behind a
    filter that happened to exclude the other row.
    """
    session = _integration_session
    owner = await _seed_user(session, OWNER)
    stranger = await _seed_user(session, STRANGER)
    mine = await _seed_lead(session, user=owner, tag="own")
    theirs = await _seed_lead(session, user=stranger, tag="other")

    async with _client(_app) as client:
        response = await client.get("/leads", headers=_headers(_app))

    assert response.status_code == 200, response.text
    assert _ids(response.json()) == [str(mine.id)]
    assert str(theirs.id) not in response.text


async def test_a_token_for_an_unprovisioned_subject_is_404(_app: FastAPI) -> None:
    """A valid signature is not an account.

    The token verifies - so this is not a 401 - but no `app_user` row has
    that `external_id`. Returning the leads of *some* user, or an empty list
    as though the account existed, would both be worse than saying so.
    """
    async with _client(_app) as client:
        response = await client.get("/leads", headers=_headers(_app, "ext-nobody"))

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 404
    assert body["detail"] == "user not found"


# --- filters -----------------------------------------------------------------


async def test_zip_filter_accepts_one_or_many_values(
    _app: FastAPI, _integration_session: AsyncSession
) -> None:
    session = _integration_session
    owner = await _seed_user(session, OWNER)
    a = await _seed_lead(session, user=owner, tag="z1", postal_code="78701")
    b = await _seed_lead(session, user=owner, tag="z2", postal_code="78702")
    await _seed_lead(session, user=owner, tag="z3", postal_code="78703")

    async with _client(_app) as client:
        one = await client.get("/leads", headers=_headers(_app), params={"zip": "78701"})
        two = await client.get(
            "/leads", headers=_headers(_app), params=[("zip", "78701"), ("zip", "78702")]
        )
        none = await client.get("/leads", headers=_headers(_app), params={"zip": "99999"})

    assert _ids(one.json()) == [str(a.id)]
    assert set(_ids(two.json())) == {str(a.id), str(b.id)}
    assert _ids(none.json()) == []
    assert none.json()["next_cursor"] is None


async def test_city_filter_accepts_one_or_many_values(
    _app: FastAPI, _integration_session: AsyncSession
) -> None:
    session = _integration_session
    owner = await _seed_user(session, OWNER)
    austin = await _seed_lead(session, user=owner, tag="c1", city="AUSTIN")
    dallas = await _seed_lead(session, user=owner, tag="c2", city="DALLAS", postal_code="75201")

    async with _client(_app) as client:
        one = await client.get("/leads", headers=_headers(_app), params={"city": "DALLAS"})
        both = await client.get(
            "/leads", headers=_headers(_app), params=[("city", "AUSTIN"), ("city", "DALLAS")]
        )

    assert _ids(one.json()) == [str(dallas.id)]
    assert set(_ids(both.json())) == {str(austin.id), str(dallas.id)}


async def test_zip_and_city_intersect_rather_than_union(
    _app: FastAPI, _integration_session: AsyncSession
) -> None:
    """Two filters must narrow, never widen.

    Worth its own test because the failure is invisible from the client's
    side: an OR returns *more* leads, every one of them a real lead, and the
    realtor simply sees inventory they did not ask for. The seeded pair is
    chosen so a union would return both and an intersection returns neither.
    """
    session = _integration_session
    owner = await _seed_user(session, OWNER)
    await _seed_lead(session, user=owner, tag="x1", city="AUSTIN", postal_code="78701")
    await _seed_lead(session, user=owner, tag="x2", city="DALLAS", postal_code="75201")

    async with _client(_app) as client:
        response = await client.get(
            "/leads", headers=_headers(_app), params={"city": "AUSTIN", "zip": "75201"}
        )

    assert _ids(response.json()) == []


async def test_persona_filter_partitions_the_pipeline(
    _app: FastAPI, _integration_session: AsyncSession
) -> None:
    session = _integration_session
    owner = await _seed_user(session, OWNER)
    flip = await _seed_lead(session, user=owner, tag="p1", persona=Persona.flipper)
    whole = await _seed_lead(session, user=owner, tag="p2", persona=Persona.wholesaler)

    async with _client(_app) as client:
        flipper = await client.get("/leads", headers=_headers(_app), params={"persona": "flipper"})
        both = await client.get("/leads", headers=_headers(_app))
        bogus = await client.get("/leads", headers=_headers(_app), params={"persona": "landlord"})

    assert _ids(flipper.json()) == [str(flip.id)]
    assert set(_ids(both.json())) == {str(flip.id), str(whole.id)}
    # An unknown persona is a client error, not an empty result: silently
    # returning `[]` for a typo'd enum is indistinguishable from "no matches".
    assert bogus.status_code == 422


async def test_min_score_includes_the_boundary(
    _app: FastAPI, _integration_session: AsyncSession
) -> None:
    """`>=`, not `>`.

    Thresholds arrive from a saved search where the user typed the number
    they wanted, so the lead sitting exactly on it is the one they most
    expect to see. `0.4999` and `0.5001` bracket the boundary at the full
    precision of `Numeric(5, 4)`, so an off-by-one-ulp comparison fails here.
    """
    session = _integration_session
    owner = await _seed_user(session, OWNER)
    below = await _seed_lead(session, user=owner, tag="s1", score="0.4999")
    exact = await _seed_lead(session, user=owner, tag="s2", score="0.5000")
    above = await _seed_lead(session, user=owner, tag="s3", score="0.5001")

    async with _client(_app) as client:
        response = await client.get("/leads", headers=_headers(_app), params={"min_score": "0.5"})
        unfiltered = await client.get("/leads", headers=_headers(_app))

    assert _ids(response.json()) == [str(above.id), str(exact.id)]
    assert _ids(unfiltered.json()) == [str(above.id), str(exact.id), str(below.id)]


@pytest.mark.parametrize("value", ["-0.0001", "1.0001", "2", "not-a-number"])
async def test_min_score_outside_zero_to_one_is_rejected(_app: FastAPI, value: str) -> None:
    async with _client(_app) as client:
        response = await client.get("/leads", headers=_headers(_app), params={"min_score": value})

    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize(
    ("limit", "expected"), [(0, 422), (-1, 422), (1, 200), (100, 200), (101, 422)]
)
async def test_limit_bounds_are_enforced(
    _app: FastAPI, _integration_session: AsyncSession, limit: int, expected: int
) -> None:
    """The ceiling is what stops one request from materializing the table.

    `limit` feeds `LIMIT limit + 1` and, for every returned row, a DTO built
    from a property and a deal analysis. Without the ceiling a single
    `?limit=100000` is a trivially available amplification.
    """
    await _seed_user(_integration_session, OWNER)

    async with _client(_app) as client:
        response = await client.get("/leads", headers=_headers(_app), params={"limit": limit})

    assert response.status_code == expected, response.text


# --- pagination --------------------------------------------------------------


async def test_paging_visits_every_lead_exactly_once_in_score_order(
    _app: FastAPI, _integration_session: AsyncSession
) -> None:
    session = _integration_session
    owner = await _seed_user(session, OWNER)
    # Descending scores, seeded in ascending order so insertion order and
    # expected output order are deliberately opposite.
    leads = [
        await _seed_lead(session, user=owner, tag=f"page-{i}", score=f"0.{i:04d}")
        for i in range(1, 8)
    ]
    expected = [str(lead.id) for lead in reversed(leads)]

    async with _client(_app) as client:
        for limit in (1, 3, 7, 100):
            walked = await _walk(client, _headers(_app), limit=limit)
            assert walked == expected, f"limit={limit}"


async def test_a_full_final_page_reports_no_further_cursor(
    _app: FastAPI, _integration_session: AsyncSession
) -> None:
    """The `limit + 1` probe, asserted at the one input that can break it.

    With exactly `limit` rows left, fetching `limit + 1` returns `limit` and
    `has_more` is False. A `>=` there would hand the client a cursor to an
    empty page - harmless-looking, and an infinite scroll that never stops.
    """
    session = _integration_session
    owner = await _seed_user(session, OWNER)
    for i in range(3):
        await _seed_lead(session, user=owner, tag=f"full-{i}", score=f"0.{i:04d}")

    async with _client(_app) as client:
        exact = await client.get("/leads", headers=_headers(_app), params={"limit": 3})
        short = await client.get("/leads", headers=_headers(_app), params={"limit": 2})

    assert len(exact.json()["items"]) == 3
    assert exact.json()["next_cursor"] is None

    assert len(short.json()["items"]) == 2
    assert short.json()["next_cursor"] is not None


async def test_equal_scores_break_ties_by_id_without_gaps_or_repeats(
    _app: FastAPI, _integration_session: AsyncSession
) -> None:
    """The tie-break, and the collation agreement it rests on.

    Every seeded lead has the same score, so `score_snapshot` orders nothing
    and the entire result depends on `id DESC` plus the cursor's
    `(score = s AND id < i)` branch. `expected` is computed by sorting
    `uuid.UUID` objects in Python while the API sorts `uuid` values in
    Postgres, so this also asserts the two agree - if they did not, a scroll
    would skip rows only when scores tie, which is exactly when a
    score-threshold query returns its densest pages.
    """
    session = _integration_session
    owner = await _seed_user(session, OWNER)
    ids = [
        (await _seed_lead(session, user=owner, tag=f"tie-{i}", score="0.5000")).id for i in range(5)
    ]
    expected = [str(i) for i in sorted(ids, reverse=True)]

    async with _client(_app) as client:
        assert await _walk(client, _headers(_app), limit=1) == expected
        assert await _walk(client, _headers(_app), limit=2) == expected


async def test_next_cursor_encodes_the_last_row_of_the_page(
    _app: FastAPI, _integration_session: AsyncSession
) -> None:
    """The cursor is opaque to clients, not to this test.

    Decoding it is the only way to distinguish "resumes after the last row
    returned" from "resumes after the last row *fetched*" - the `limit + 1`
    probe row. The second form silently drops one lead per page boundary and
    is invisible unless the walk is compared against the full expected set.
    """
    session = _integration_session
    owner = await _seed_user(session, OWNER)
    for i in range(4):
        await _seed_lead(session, user=owner, tag=f"cur-{i}", score=f"0.{i:04d}")

    async with _client(_app) as client:
        page = await client.get("/leads", headers=_headers(_app), params={"limit": 2})

    body = page.json()
    last = body["items"][-1]
    decoded = LeadCursor.decode(body["next_cursor"])
    assert decoded.id == uuid.UUID(last["id"])
    assert decoded.score == Decimal(last["score"])


async def test_filters_survive_the_cursor_round_trip(
    _app: FastAPI, _integration_session: AsyncSession
) -> None:
    """Page two must be filtered like page one.

    The cursor carries only `(score, id)`, so the filters have to be re-sent
    on every request. Seeding an interleaved population - a matching and a
    non-matching lead at each score - means a page-two request that forgot
    its filter returns the wrong rows rather than merely more of them.
    """
    session = _integration_session
    owner = await _seed_user(session, OWNER)
    wanted: list[str] = []
    for i in range(1, 5):
        keep = await _seed_lead(
            session, user=owner, tag=f"f-in-{i}", postal_code="78701", score=f"0.{i:04d}"
        )
        wanted.append(str(keep.id))
        await _seed_lead(
            session, user=owner, tag=f"f-out-{i}", postal_code="78702", score=f"0.{i:04d}"
        )

    async with _client(_app) as client:
        walked = await _walk(client, _headers(_app), limit=1, zip="78701")

    assert walked == list(reversed(wanted))


async def test_a_malformed_cursor_is_a_client_error(
    _app: FastAPI, _integration_session: AsyncSession
) -> None:
    """Client-supplied opacity must not become a 5xx.

    The cursor is base64 over JSON, so a truncated URL, a stale bookmark, or
    a client that percent-decodes twice all arrive here as garbage. Decoding
    it can raise from `binascii`, `json`, `decimal`, or `uuid`; every one of
    those is the *client's* error, and letting it reach the unhandled-
    exception handler would spend the service's error budget on a bad link.
    """
    await _seed_user(_integration_session, OWNER)

    async with _client(_app) as client:
        for bad in (
            "not-base64!!",
            "",
            "e30",
            LeadCursor(Decimal("0.5"), uuid.uuid4()).encode()[:6],
        ):
            response = await client.get("/leads", headers=_headers(_app), params={"cursor": bad})
            assert response.status_code == 400, f"{bad!r} -> {response.status_code}"
            assert response.headers["content-type"].startswith("application/problem+json")


# --- payload shape -----------------------------------------------------------


async def test_deal_summary_reflects_the_highest_analysis_version(
    _app: FastAPI, _integration_session: AsyncSession
) -> None:
    """Analyses are append-only, so "the analysis" means the newest one.

    `deal_analysis` is versioned rather than updated in place, and the route
    picks a winner per property by ordering on `analysis_version DESC` and
    keeping the first row seen. Seeding v2 *before* v1 means a route that
    relied on insertion order would pass on the ordering and fail here.
    """
    session = _integration_session
    owner = await _seed_user(session, OWNER)
    analysed = await _seed_lead(session, user=owner, tag="deal-yes", score="0.9000")
    plain = await _seed_lead(session, user=owner, tag="deal-no", score="0.1000")

    for version, rehab_low, condition in ((2, 40_000_00, "C5"), (1, 25_000_00, "C4")):
        session.add(
            DealAnalysis(
                property_id=analysed.property_id,
                analysis_version=version,
                model_id="claude-sonnet-4-5",
                prompt_version="photo_grader_v1",
                overall_condition=condition,
                rehab_low_cents=rehab_low,
                rehab_high_cents=rehab_low + 10_000_00,
                avm_value_cents=300_000_00,
                arv_cents=320_000_00,
                rehab_line_items=[],
                comps=[],
                red_flags=["foundation"],
                quality_gate_flags=[],
            )
        )
    await session.flush()

    async with _client(_app) as client:
        body = (await client.get("/leads", headers=_headers(_app))).json()

    by_id = {item["id"]: item for item in body["items"]}
    summary = by_id[str(analysed.id)]["deal_summary"]
    assert summary["overall_condition"] == "C5"
    assert summary["rehab_low_cents"] == 40_000_00
    assert summary["red_flags"] == ["foundation"]
    # A property nobody has analysed yet is still a lead; the summary is
    # absent rather than the row being dropped.
    assert by_id[str(plain.id)]["deal_summary"] is None


async def test_list_item_carries_the_mapped_property_dto(
    _app: FastAPI, _integration_session: AsyncSession
) -> None:
    """`property_to_dto` runs on this path, and its output is the contract.

    `display_address` exists only in the DTO - no column holds it - so
    asserting its exact text is what proves the mapper ran rather than the
    ORM row being serialized directly.
    """
    session = _integration_session
    owner = await _seed_user(session, OWNER)
    lead = await _seed_lead(session, user=owner, tag="dto", score="0.6250")

    async with _client(_app) as client:
        body = (await client.get("/leads", headers=_headers(_app))).json()

    item = body["items"][0]
    assert item["id"] == str(lead.id)
    assert item["persona"] == "flipper"
    assert item["status"] == "new"
    assert Decimal(item["score"]) == Decimal("0.6250")
    prop = item["property"]
    assert prop["display_address"] == "123 MAIN ST, AUSTIN, TX, 78701"
    assert prop["latitude"] is None and prop["longitude"] is None
    # Nothing computes a total yet; the field is declared so the frontend can
    # render a count later without a breaking change, and it must stay null
    # rather than becoming a page-sized number that looks like one.
    assert body["total_estimate"] is None


async def test_an_empty_pipeline_is_an_empty_page_not_an_error(
    _app: FastAPI, _integration_session: AsyncSession
) -> None:
    await _seed_user(_integration_session, OWNER)

    async with _client(_app) as client:
        response = await client.get("/leads", headers=_headers(_app))

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None, "total_estimate": None}
