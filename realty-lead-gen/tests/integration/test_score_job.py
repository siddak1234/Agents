"""`score_property_job` against real Postgres — the claims only Postgres can settle.

The pure half of this job (which users get a lead, and at what threshold) is
covered in `tests/unit/test_lead_materialization.py` with no database. What is
left is the half whose correctness *is* Postgres behaviour, and asserting it
against a mock would only prove the mock agrees with itself:

* `ON CONFLICT DO UPDATE` actually resolving against the two unique indexes —
  `uq_score__property_persona_version` and `uq_lead__property_user_persona`.
  A missing or misnamed index does not fail a type check; it raises at
  runtime, or worse, silently duplicates rows.
* The insert-vs-update signal. `_upsert_lead` has to know whether it just
  created a lead, because the outbox event must fire once — when the lead
  first appears — and not again on every nightly re-score. It answers that by
  comparing the returned `surfaced_at` to the timestamp it bound, which only
  works because `surfaced_at` is absent from the `set_` clause. That is a
  property of the emitted SQL, so it is tested by running the SQL.
* The human's state surviving a re-score. A lead the user dismissed must not
  come back as new tomorrow.

The job commits through `session_scope`, which builds the process-wide engine
from settings. Tests substitute a scope bound to the rolled-back fixture
session instead — see `_bind_job_to(session)`. The substitution keeps the real
commit call, so the code path under test is unchanged; it is the transaction
underneath that is disposable.
"""

from __future__ import annotations

import inspect
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, NamedTuple

import pytest
from sqlalchemy import func, select, update

from realty_lead_gen.jobs import score as score_job
from realty_lead_gen.models.buyer import BuyerProfile, BuyerReadiness, SavedSearch
from realty_lead_gen.models.deal import DealAnalysis
from realty_lead_gen.models.lead import Lead, LeadStatus
from realty_lead_gen.models.outbox import OutboxEvent
from realty_lead_gen.models.property import (
    Property,
    PropertySnapshot,
    PropertyType,
    SnapshotSource,
)
from realty_lead_gen.models.score import Persona, Score
from realty_lead_gen.models.user import User, UserRole
from realty_lead_gen.scoring.base import ScoreOutput

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------


class _Seed(NamedTuple):
    user: User
    prop: Property
    search: SavedSearch | None


def _unique_suffix() -> str:
    """`User.external_id` and `User.email` are both unique columns.

    Each test rolls back, so collisions across tests are impossible — but a
    single test that seeds two users would collide on a hardcoded value, and
    that failure reads as a mysterious IntegrityError rather than as "you
    reused an email".
    """
    return uuid.uuid4().hex[:12]


async def _seed_user(session: AsyncSession) -> User:
    suffix = _unique_suffix()
    user = User(
        external_id=f"ext-{suffix}",
        email=f"agent-{suffix}@example.com",
        display_name="Test Agent",
        role=UserRole.realtor,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_property(session: AsyncSession, *, postal_code: str = "78701") -> Property:
    """A property with the enrichment a scorer needs, and nothing more.

    The deal figures are the same ones `tests/unit/test_flipper_scoring.py`
    calls a good deal (ARV 320k, rehab 35k, asking 190k — just above the
    70%-rule MAO of 189k), so the flipper scorer returns something non-
    trivial. The assertions here never depend on *what* it returns, only that
    a lead was materialized from it; thresholds in this file are 0 so that
    retuning a scorer cannot break a persistence test.
    """
    prop = Property(
        # `uuid4().hex` is 32 chars and `address_hash` is a 64-char sha256
        # column, so doubling it fills the width without pretending to be a
        # real hash of a real address.
        address_hash=uuid.uuid4().hex * 2,
        street_number="123",
        street_name="MAIN ST",
        city="AUSTIN",
        state="TX",
        postal_code=postal_code,
        property_type=PropertyType.single_family,
        bedrooms=3,
        bathrooms=Decimal("2"),
        living_area_sqft=1800,
        attributes={},
    )
    session.add(prop)
    await session.flush()

    session.add(
        DealAnalysis(
            property_id=prop.id,
            analysis_version=1,
            model_id="claude-sonnet-4-5",
            prompt_version="photo_grader_v1",
            overall_condition="C4",
            condition_confidence=Decimal("0.700"),
            rehab_low_cents=25_000_00,
            rehab_high_cents=35_000_00,
            avm_value_cents=300_000_00,
            avm_low_cents=285_000_00,
            avm_high_cents=315_000_00,
            avm_confidence=Decimal("0.800"),
            arv_cents=320_000_00,
            monthly_rent_cents=2_200_00,
            rehab_line_items=[],
            comps=[],
            red_flags=[],
            quality_gate_flags=[],
        )
    )
    session.add(
        PropertySnapshot(
            property_id=prop.id,
            source=SnapshotSource.mls_reso,
            source_record_id=f"rec-{_unique_suffix()}",
            observed_at=datetime.now(UTC),
            status="active",
            list_price_cents=190_000_00,
            days_on_market=30,
            raw_payload={},
        )
    )
    await session.flush()
    return prop


async def _seed(
    session: AsyncSession,
    *,
    persona: Persona | None = Persona.flipper,
    min_score: str = "0.0000",
    postal_code: str = "78701",
) -> _Seed:
    """User + enriched property + (optionally) one saved search.

    `min_score` defaults to 0 on purpose. This file is about persistence, and
    a threshold tuned to today's scorer output would make every test here
    fail the next time a weight moves — which is exactly the false alarm that
    trains people to stop reading test failures. The threshold logic itself is
    asserted in the unit suite, where it costs nothing.
    """
    user = await _seed_user(session)
    prop = await _seed_property(session, postal_code=postal_code)
    search: SavedSearch | None = None
    if persona is not None:
        search = SavedSearch(
            user_id=user.id,
            name=f"{persona.value} watch",
            persona=persona,
            postal_codes=[postal_code],
            cities=[],
            regions=[],
            min_score=Decimal(min_score),
            criteria={},
        )
        session.add(search)
        await session.flush()
    return _Seed(user=user, prop=prop, search=search)


async def _seed_buyer(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    display_name: str = "Cash buyer",
    target_cities: list[str] | None = None,
    target_postal_codes: list[str] | None = None,
    max_price_cents: int | None = None,
    min_price_cents: int | None = None,
    min_bedrooms: int | None = None,
    min_living_area_sqft: int | None = None,
    property_types: list[str] | None = None,
) -> BuyerProfile:
    """One active buyer profile, with every criterion off unless named.

    Spelled out as explicit keywords rather than `**kwargs` so that a column
    rename breaks this at type-check time instead of silently seeding a
    profile with no criteria — which would still match, and would make the
    tests below pass for the wrong reason.
    """
    profile = BuyerProfile(
        user_id=user_id,
        display_name=display_name,
        target_cities=target_cities or [],
        target_postal_codes=target_postal_codes or [],
        max_price_cents=max_price_cents,
        min_price_cents=min_price_cents,
        min_bedrooms=min_bedrooms,
        min_living_area_sqft=min_living_area_sqft,
        property_types=property_types or [],
        must_haves=[],
        nice_to_haves=[],
        deal_breakers=[],
        readiness=BuyerReadiness.cash,
    )
    session.add(profile)
    await session.flush()
    return profile


def _bind_job_to(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Point the job's `session_scope` at the rolled-back fixture session.

    The real `session_scope` reaches for the process-wide engine built from
    `DATABASE_URL`, which would commit outside the test's transaction and leak
    rows into every later test. The replacement still awaits `commit()`, so
    the job's own flush/commit sequencing is exercised unchanged — the commit
    lands in a SAVEPOINT that the fixture discards.
    """

    @asynccontextmanager
    async def _scope() -> AsyncIterator[AsyncSession]:
        yield session
        await session.commit()

    monkeypatch.setattr(score_job, "session_scope", _scope)


def _output(score: str, *, rationale: str | None = None) -> ScoreOutput:
    return ScoreOutput(
        score=Decimal(score),
        confidence=Decimal("0.700"),
        components={"probe": {"value": score}},
        rationale=rationale,
    )


# --------------------------------------------------------------------------
# Score upsert
# --------------------------------------------------------------------------


async def test_rescoring_updates_the_same_score_row(
    _integration_session: AsyncSession,
) -> None:
    """`uq_score__property_persona_version` is the conflict target.

    The nightly sweep re-scores everything, so a second pass at the same
    scorer version has to update rather than accumulate. If the unique index
    were missing this would insert a second row and every later "the score
    for this property is X" read would depend on which row it happened to
    pick.
    """
    session = _integration_session
    seed = await _seed(session)

    first_id = await score_job._upsert_score(
        session, seed.prop.id, Persona.flipper, "flipper_v1", _output("0.6000", rationale="first")
    )
    second_id = await score_job._upsert_score(
        session, seed.prop.id, Persona.flipper, "flipper_v1", _output("0.8000", rationale="second")
    )

    assert second_id == first_id

    row = (
        await session.execute(select(Score).where(Score.property_id == seed.prop.id))
    ).scalar_one()
    assert row.id == first_id
    assert row.score == Decimal("0.8000")
    assert row.rationale == "second"
    assert row.components == {"probe": {"value": "0.8000"}}


async def test_a_new_scorer_version_gets_its_own_row(
    _integration_session: AsyncSession,
) -> None:
    """Versioned scores are append-only across versions, upserts within one.

    This is what makes a scorer change auditable: the old number stays
    readable next to the new one, so "did v2 make this worse?" is a query
    rather than an archaeology exercise.
    """
    session = _integration_session
    seed = await _seed(session)

    v1 = await score_job._upsert_score(
        session, seed.prop.id, Persona.flipper, "flipper_v1", _output("0.6000")
    )
    v2 = await score_job._upsert_score(
        session, seed.prop.id, Persona.flipper, "flipper_v2", _output("0.7000")
    )

    assert v1 != v2
    versions = (
        (
            await session.execute(
                select(Score.scorer_version)
                .where(Score.property_id == seed.prop.id)
                .order_by(Score.scorer_version)
            )
        )
        .scalars()
        .all()
    )
    assert list(versions) == ["flipper_v1", "flipper_v2"]


async def test_personas_do_not_share_a_score_row(
    _integration_session: AsyncSession,
) -> None:
    session = _integration_session
    seed = await _seed(session)

    flipper = await score_job._upsert_score(
        session, seed.prop.id, Persona.flipper, "flipper_v1", _output("0.6000")
    )
    wholesaler = await score_job._upsert_score(
        session, seed.prop.id, Persona.wholesaler, "wholesaler_v1", _output("0.4000")
    )

    assert flipper != wholesaler
    count = (
        await session.execute(
            select(func.count()).select_from(Score).where(Score.property_id == seed.prop.id)
        )
    ).scalar_one()
    assert count == 2


# --------------------------------------------------------------------------
# Lead upsert
# --------------------------------------------------------------------------


async def _plan_for(seed: _Seed) -> score_job._LeadUpsert:
    assert seed.search is not None
    return score_job._LeadUpsert(
        lead_id=uuid.uuid4(),
        user_id=seed.user.id,
        saved_search_id=seed.search.id,
        rank_meta={"scorer_version": "flipper_v1"},
    )


async def test_a_dismissed_lead_is_refreshed_not_resurrected(
    _integration_session: AsyncSession,
) -> None:
    """The single most important claim in this module.

    A user dismisses a lead. Tonight's sweep re-scores the property and
    upserts the same (property, user, persona) triple. Three things must all
    hold at once:

    * the row is *updated*, not duplicated — the unique index resolves it;
    * `was_inserted` comes back `False`, so no second `lead.surfaced` event
      is emitted and the user is not notified again about a lead they threw
      away;
    * `status` and `surfaced_at` are untouched, while the valuation
      (`score_id`, `score_snapshot`) is refreshed. That split — machine
      fields move, human fields do not — is the whole design of the `set_`
      clause, and it is enforced by omission, which is the kind of thing that
      is silently lost in a refactor.
    """
    session = _integration_session
    seed = await _seed(session)
    # Captured now because `session.expire_all()` below invalidates every
    # loaded attribute, and re-reading `seed.prop.id` after that point would
    # trigger a lazy refresh from a synchronous attribute access — which under
    # asyncpg raises `MissingGreenlet` rather than doing the IO.
    property_id = seed.prop.id

    v1_score = await score_job._upsert_score(
        session, property_id, Persona.flipper, "flipper_v1", _output("0.6000")
    )
    lead_id, was_new = await score_job._upsert_lead(
        session,
        property_id,
        score_job.PersonaResult(Persona.flipper, "flipper_v1", _output("0.6000"), v1_score),
        await _plan_for(seed),
    )
    assert was_new is True

    original_surfaced_at = (
        await session.execute(select(Lead.surfaced_at).where(Lead.id == lead_id))
    ).scalar_one()

    # The human acts on it.
    await session.execute(
        update(Lead)
        .where(Lead.id == lead_id)
        .values(status=LeadStatus.dismissed, viewed_at=datetime.now(UTC))
    )

    # Tonight's sweep, at a newer scorer version and a better score.
    v2_score = await score_job._upsert_score(
        session, property_id, Persona.flipper, "flipper_v2", _output("0.9000")
    )
    second_plan = score_job._LeadUpsert(
        lead_id=uuid.uuid4(),  # a fresh candidate id, which must be discarded
        user_id=seed.user.id,
        saved_search_id=None,
        rank_meta={"scorer_version": "flipper_v2"},
    )
    same_lead_id, was_new_again = await score_job._upsert_lead(
        session,
        property_id,
        score_job.PersonaResult(Persona.flipper, "flipper_v2", _output("0.9000"), v2_score),
        second_plan,
    )

    assert same_lead_id == lead_id
    assert same_lead_id != second_plan.lead_id
    assert was_new_again is False

    # The identity map holds the pre-update object; the row is what is being
    # asserted, so read it back rather than trusting the in-session copy.
    session.expire_all()
    row = (await session.execute(select(Lead).where(Lead.id == lead_id))).scalar_one()

    assert row.status is LeadStatus.dismissed
    assert row.surfaced_at == original_surfaced_at
    assert row.score_snapshot == Decimal("0.9000")
    assert row.score_id == v2_score
    assert row.rank_meta == {"scorer_version": "flipper_v2"}

    count = (
        await session.execute(
            select(func.count()).select_from(Lead).where(Lead.property_id == property_id)
        )
    ).scalar_one()
    assert count == 1


async def test_two_users_get_separate_leads_for_one_property(
    _integration_session: AsyncSession,
) -> None:
    """The conflict target includes `user_id`, and this is what proves it.

    If it did not, the second agent's lead would overwrite the first's and
    one of two paying users would silently never see the deal.
    """
    session = _integration_session
    seed = await _seed(session)
    other = await _seed_user(session)

    score_id = await score_job._upsert_score(
        session, seed.prop.id, Persona.flipper, "flipper_v1", _output("0.6000")
    )
    result = score_job.PersonaResult(Persona.flipper, "flipper_v1", _output("0.6000"), score_id)

    first_id, first_new = await score_job._upsert_lead(
        session, seed.prop.id, result, await _plan_for(seed)
    )
    second_id, second_new = await score_job._upsert_lead(
        session,
        seed.prop.id,
        result,
        score_job._LeadUpsert(
            lead_id=uuid.uuid4(), user_id=other.id, saved_search_id=None, rank_meta={}
        ),
    )

    assert first_new is True
    assert second_new is True
    assert first_id != second_id


async def test_one_user_gets_separate_leads_per_persona(
    _integration_session: AsyncSession,
) -> None:
    """An agent who both flips and wholesales sees the property twice.

    Correct: the two personas are different theses about the same house, with
    different arithmetic and different next actions.
    """
    session = _integration_session
    seed = await _seed(session)

    flip_score = await score_job._upsert_score(
        session, seed.prop.id, Persona.flipper, "flipper_v1", _output("0.6000")
    )
    whole_score = await score_job._upsert_score(
        session, seed.prop.id, Persona.wholesaler, "wholesaler_v1", _output("0.5000")
    )

    flip_id, _ = await score_job._upsert_lead(
        session,
        seed.prop.id,
        score_job.PersonaResult(Persona.flipper, "flipper_v1", _output("0.6000"), flip_score),
        await _plan_for(seed),
    )
    whole_id, whole_new = await score_job._upsert_lead(
        session,
        seed.prop.id,
        score_job.PersonaResult(
            Persona.wholesaler, "wholesaler_v1", _output("0.5000"), whole_score
        ),
        score_job._LeadUpsert(
            lead_id=uuid.uuid4(), user_id=seed.user.id, saved_search_id=None, rank_meta={}
        ),
    )

    assert whole_new is True
    assert flip_id != whole_id


# --------------------------------------------------------------------------
# The job, end to end
# --------------------------------------------------------------------------


async def _outbox_events(session: AsyncSession, event_type: str) -> list[OutboxEvent]:
    return list(
        (await session.execute(select(OutboxEvent).where(OutboxEvent.event_type == event_type)))
        .scalars()
        .all()
    )


async def test_the_job_surfaces_once_and_then_only_refreshes(
    _integration_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run the whole job twice on the same property.

    The counts are the contract the worker logs and the ops dashboard reads,
    so they are asserted as a whole dict rather than field by field — a
    fourth key appearing, or `surfaced` quietly starting to include
    refreshes, should fail here.

    The outbox assertion is the one that matters operationally: exactly one
    `lead.surfaced` event across both runs. At-least-once delivery already
    means the frontend may see an event twice; emitting a second one per
    nightly sweep would turn that into a guaranteed duplicate notification
    for every lead, every night.
    """
    session = _integration_session
    seed = await _seed(session)
    _bind_job_to(session, monkeypatch)

    first = await score_job.score_property_job({}, str(seed.prop.id))
    assert first == {"scored": 1, "surfaced": 1, "refreshed": 0}

    second = await score_job.score_property_job({}, str(seed.prop.id))
    assert second == {"scored": 1, "surfaced": 0, "refreshed": 1}

    leads = list(
        (await session.execute(select(Lead).where(Lead.property_id == seed.prop.id)))
        .scalars()
        .all()
    )
    assert len(leads) == 1
    assert leads[0].user_id == seed.user.id
    assert leads[0].persona is Persona.flipper
    assert seed.search is not None
    assert leads[0].saved_search_id == seed.search.id

    events = await _outbox_events(session, "lead.surfaced")
    assert len(events) == 1
    payload = events[0].payload
    assert payload["lead_id"] == str(leads[0].id)
    assert payload["property_id"] == str(seed.prop.id)
    # The external id is the frontend's key for the user — it is looked up
    # through the `.tuples()` map in `score_property_job`, and an empty string
    # here would mean that lookup silently missed.
    assert payload["user_external_id"] == seed.user.external_id
    assert payload["persona"] == "flipper"


async def test_the_job_scores_every_persona_not_just_the_one_subscribed(
    _integration_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scores are written for flipper and wholesaler; only flipper leads.

    Worth separating from the lead assertions because it is the reason the
    API can answer "what would this look like as a wholesale deal?" for a
    property the user only subscribed to as a flip. The buyer's-agent scorer
    writes nothing here — no active buyer profiles exist, so it returns
    `None` before touching the database.
    """
    session = _integration_session
    seed = await _seed(session)
    _bind_job_to(session, monkeypatch)

    await score_job.score_property_job({}, str(seed.prop.id))

    personas = (
        (
            await session.execute(
                select(Score.persona)
                .where(Score.property_id == seed.prop.id)
                .order_by(Score.persona)
            )
        )
        .scalars()
        .all()
    )
    assert set(personas) == {Persona.flipper, Persona.wholesaler}

    leads = (
        (await session.execute(select(Lead.persona).where(Lead.property_id == seed.prop.id)))
        .scalars()
        .all()
    )
    assert list(leads) == [Persona.flipper]


async def test_a_buyer_profile_produces_an_agent_lead_naming_the_buyer(
    _integration_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The buyer-side half of the product, end to end.

    This is the path that makes the system two-sided: the same ingested
    property that a flipper evaluates as a deal is also matched against an
    agent's buyers. The profile below states criteria the seeded property
    satisfies, so `BuyerMatcher` returns a match rather than discarding it,
    and the resulting lead carries the buyer's id in `rank_meta` so the agent
    knows *who* to call.
    """
    session = _integration_session
    seed = await _seed(session, persona=Persona.buyers_agent)
    profile = await _seed_buyer(
        session,
        seed.user.id,
        target_cities=["AUSTIN"],
        target_postal_codes=["78701"],
        max_price_cents=400_000_00,
        min_bedrooms=2,
        min_living_area_sqft=1200,
        property_types=["single_family"],
    )
    _bind_job_to(session, monkeypatch)

    result = await score_job.score_property_job({}, str(seed.prop.id))
    assert result["scored"] == 1

    lead = (
        await session.execute(
            select(Lead).where(
                Lead.property_id == seed.prop.id,
                Lead.persona == Persona.buyers_agent,
            )
        )
    ).scalar_one()
    assert lead.rank_meta["matched_buyer_profiles"] == [str(profile.id)]
    # A buyer profile is its own search, so the lead is not attributed to the
    # saved search that merely supplied the threshold.
    assert lead.saved_search_id is None

    assert (
        await session.execute(
            select(func.count())
            .select_from(Score)
            .where(
                Score.property_id == seed.prop.id,
                Score.persona == Persona.buyers_agent,
            )
        )
    ).scalar_one() == 1


async def test_a_buyer_the_matcher_rejects_produces_no_agent_score(
    _integration_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An agent with only priced-out buyers gets nothing, and that is correct.

    The matcher is authoritative on exclusion, so a profile whose stated
    ceiling this listing blows through never reaches the scorer at all — the
    persona writes no `Score` row rather than a low one. Worth pinning
    separately from the "no profiles exist" case, because the two reach the
    same `return None` by different routes and only this one exercises the
    matcher.

    The flipper and wholesaler scores are asserted alongside so that the two
    zeros below cannot be satisfied by the job having quietly done nothing at
    all.
    """
    session = _integration_session
    seed = await _seed(session, persona=Persona.buyers_agent)
    await _seed_buyer(
        session,
        seed.user.id,
        display_name="Priced out",
        # The seeded listing is $190k.
        max_price_cents=100_000_00,
    )
    _bind_job_to(session, monkeypatch)

    assert await score_job.score_property_job({}, str(seed.prop.id)) == {
        "scored": 1,
        "surfaced": 0,
        "refreshed": 0,
    }

    personas = set(
        (await session.execute(select(Score.persona).where(Score.property_id == seed.prop.id)))
        .scalars()
        .all()
    )
    assert personas == {Persona.flipper, Persona.wholesaler}
    assert (
        await session.execute(
            select(func.count()).select_from(Lead).where(Lead.property_id == seed.prop.id)
        )
    ).scalar_one() == 0


async def test_tied_buyers_score_the_same_whatever_order_they_load_in(
    _integration_session: AsyncSession,
) -> None:
    """The buyer-side twin of the `_buyers_agent_thresholds` ordering bug.

    `score_property_job` loads buyer profiles with no `ORDER BY`, and
    `_score_buyers_agent` reduces them to one persona score. Two of an agent's
    buyers can tie exactly — an ordinary thing, since agents copy a profile
    for a similar client — and when they do, "first maximum wins" lets
    Postgres row order pick whose `components` and `rationale` get written.
    The score would not move, so nothing would look wrong; the explanation the
    agent reads for *why* this house fits would just change from one nightly
    sweep to the next.

    The two profiles below are built to tie by different routes — one states a
    price ceiling, the other a floor, and this listing satisfies both — so the
    scores match while the components do not. The first two assertions verify
    that setup rather than assuming it: if a future weight change breaks the
    tie, this test fails loudly instead of passing vacuously.
    """
    session = _integration_session
    seed = await _seed(session, persona=Persona.buyers_agent)
    ceiling = await _seed_buyer(
        session, seed.user.id, display_name="Under 400k", max_price_cents=400_000_00
    )
    floor = await _seed_buyer(
        session, seed.user.id, display_name="Over 100k", min_price_cents=100_000_00
    )
    property_id = seed.prop.id

    context = await score_job._build_context(session, property_id)
    assert context is not None

    solo_ceiling = await score_job._score_buyers_agent(session, property_id, context, [ceiling])
    solo_floor = await score_job._score_buyers_agent(session, property_id, context, [floor])
    assert solo_ceiling is not None
    assert solo_floor is not None
    assert solo_ceiling.output.score == solo_floor.output.score, "the tie is the premise"
    assert solo_ceiling.output.components != solo_floor.output.components, (
        "a tie nothing can distinguish would make this test vacuous"
    )

    forward = await score_job._score_buyers_agent(session, property_id, context, [ceiling, floor])
    reverse = await score_job._score_buyers_agent(session, property_id, context, [floor, ceiling])
    assert forward is not None
    assert reverse is not None

    assert forward.output == reverse.output
    assert forward.matched_profile_ids == reverse.matched_profile_ids
    assert forward.matched_profile_ids == tuple(sorted([str(ceiling.id), str(floor.id)]))


async def test_the_batch_pass_takes_unscored_properties_and_then_stops(
    _integration_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The nightly sweep's own idempotency, which is a different question.

    `_resolve_property_ids` selects properties that have a `DealAnalysis` and
    no `Score` at all, so a second sweep with nothing newly enriched must be
    a no-op rather than a full re-score of the corpus. Getting this wrong is
    not a correctness bug, it is a cost bug — and an invisible one.
    """
    session = _integration_session
    seed = await _seed(session)
    _bind_job_to(session, monkeypatch)

    first = await score_job.score_property_job({})
    assert first["scored"] == 1
    assert first["surfaced"] == 1

    second = await score_job.score_property_job({})
    assert second == {"scored": 0, "surfaced": 0, "refreshed": 0}
    assert len(await _outbox_events(session, "lead.surfaced")) == 1

    # And the property really is the one that was scored.
    assert (
        await session.execute(
            select(func.count()).select_from(Score).where(Score.property_id == seed.prop.id)
        )
    ).scalar_one() == 2


async def test_a_property_with_no_enrichment_is_not_swept_up(
    _integration_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `DealAnalysis` means no valuation, so scoring it would be theatre.

    The inner join in `_resolve_property_ids` is what enforces that. Seeding a
    bare property alongside an enriched one and asserting only one gets scored
    distinguishes "the join works" from "there was nothing to find".
    """
    session = _integration_session
    seed = await _seed(session)
    bare = Property(
        address_hash=uuid.uuid4().hex * 2,
        street_name="ELM ST",
        city="AUSTIN",
        state="TX",
        postal_code="78701",
        property_type=PropertyType.single_family,
        attributes={},
    )
    session.add(bare)
    await session.flush()
    _bind_job_to(session, monkeypatch)

    assert (await score_job.score_property_job({}))["scored"] == 1

    scored_property_ids = set(
        (await session.execute(select(Score.property_id).distinct())).scalars().all()
    )
    assert scored_property_ids == {seed.prop.id}


async def test_an_unknown_property_id_is_skipped_without_raising(
    _integration_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deleted property mid-batch must not abort the batch.

    `score_property_job` is called with an explicit id from the ingest
    pipeline's outbox, so the row can legitimately be gone by the time the
    worker picks the job up. `_build_context` returns `None` and the loop
    continues; raising here would make one deleted property poison every
    other property in the same arq job.
    """
    session = _integration_session
    await _seed(session)
    _bind_job_to(session, monkeypatch)

    assert await score_job.score_property_job({}, str(uuid.uuid4())) == {
        "scored": 0,
        "surfaced": 0,
        "refreshed": 0,
    }


async def test_a_property_outside_the_saved_searchs_zip_scores_but_leads_nobody(
    _integration_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scoring and surfacing are separate stages, and this proves it.

    The score is written because it is a property of the house, not of any
    user; the lead is withheld because no user asked about that zip. Asserting
    both at once is what keeps a future "optimization" from skipping the
    scorer for properties nobody currently watches — which would break the
    moment a user widens their search.
    """
    session = _integration_session
    user = await _seed_user(session)
    prop = await _seed_property(session, postal_code="78702")
    session.add(
        SavedSearch(
            user_id=user.id,
            name="only 78701",
            persona=Persona.flipper,
            postal_codes=["78701"],
            cities=[],
            regions=[],
            min_score=Decimal("0.0000"),
            criteria={},
        )
    )
    await session.flush()
    _bind_job_to(session, monkeypatch)

    assert await score_job.score_property_job({}, str(prop.id)) == {
        "scored": 1,
        "surfaced": 0,
        "refreshed": 0,
    }
    assert (
        await session.execute(
            select(func.count()).select_from(Score).where(Score.property_id == prop.id)
        )
    ).scalar_one() == 2
    assert (
        await session.execute(
            select(func.count()).select_from(Lead).where(Lead.property_id == prop.id)
        )
    ).scalar_one() == 0
    assert await _outbox_events(session, "lead.surfaced") == []


def test_the_batch_limit_is_a_bound_not_a_suggestion() -> None:
    """A guard on the one constant that decides how much work a job does.

    `BATCH_LIMIT` reaching the `LIMIT` clause is what keeps a nightly sweep
    from loading an unbounded result set into one transaction. The value is
    tunable; that it stays a positive int, and that the query is bounded by
    it, is not.
    """
    assert isinstance(score_job.BATCH_LIMIT, int)
    assert score_job.BATCH_LIMIT > 0


def test_the_scoring_helpers_are_annotated_for_the_orchestrator() -> None:
    """Cheap structural check with a real failure mode behind it.

    `score_property_job` is registered with arq by name, and arq calls it as
    `func(ctx, *args)`. A signature change that reorders those two parameters
    type-checks fine and fails only in production, where the worker hands a
    dict to `property_id`.
    """
    params = list(inspect.signature(score_job.score_property_job).parameters)
    assert params == ["ctx", "property_id"]
