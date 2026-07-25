"""Score properties across every persona and materialize Lead rows.

Three personas run off one shared ``PropertyContext``:

    * flipper      — deterministic 70%-rule / MAO arithmetic
    * wholesaler   — assignment-spread and motivation arithmetic
    * buyers_agent — per-BuyerProfile fit, one profile at a time

Scoring is pure: every scorer takes a context and returns a
``ScoreOutput`` with no I/O. That is what lets the property-based tests in
``tests/unit`` assert invariants over generated inputs. All persistence
happens here, in one place.

Both writes are genuine upserts. Re-scoring a property must be safe to do
any number of times — the nightly sweep does exactly that — so ``score``
conflicts on ``(property_id, persona, scorer_version)`` and ``lead``
conflicts on ``(property_id, user_id, persona)``. Crucially, a lead upsert
refreshes the score but never resets ``status`` or ``surfaced_at``: a lead
the user already dismissed does not come back as new tomorrow.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final, NamedTuple

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from realty_lead_gen.db import session_scope
from realty_lead_gen.logging import get_logger
from realty_lead_gen.matching.buyer_intent import BuyerMatch, BuyerMatcher
from realty_lead_gen.models.buyer import BuyerProfile, SavedSearch
from realty_lead_gen.models.deal import DealAnalysis
from realty_lead_gen.models.lead import Lead, LeadStatus
from realty_lead_gen.models.property import Property, PropertySnapshot
from realty_lead_gen.models.score import Persona, Score
from realty_lead_gen.models.signal import Signal
from realty_lead_gen.models.user import User
from realty_lead_gen.pipeline.outbox import emit_event, lead_surfaced_payload
from realty_lead_gen.scoring.agent import BuyersAgentContext, BuyersAgentScorer
from realty_lead_gen.scoring.base import PropertyContext, ScoreOutput
from realty_lead_gen.scoring.flipper import FlipperScorer
from realty_lead_gen.scoring.wholesaler import WholesalerScorer

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

#: Applied to buyer's-agent leads when the user has no active buyers_agent
#: saved search to take a threshold from. A buyer profile is itself the
#: search, so we still want to surface matches — just not weak ones.
DEFAULT_BUYERS_AGENT_MIN_SCORE: Final[Decimal] = Decimal("0.55")

#: Bound on how many un-scored properties one batch pass will handle.
BATCH_LIMIT: Final[int] = 100


class PersonaResult(NamedTuple):
    persona: Persona
    scorer_version: str
    output: ScoreOutput
    score_id: uuid.UUID
    #: Buyer profiles that produced this score, for buyers_agent only.
    matched_profile_ids: tuple[str, ...] = ()


async def score_property_job(
    ctx: dict[str, Any],
    property_id: str | None = None,
) -> dict[str, Any]:
    """Score a specific property (if id given) or a batch of un-scored ones."""
    scored = 0
    surfaced = 0
    refreshed = 0

    async with session_scope() as session:
        prop_ids = await _resolve_property_ids(session, property_id)
        if not prop_ids:
            return {"scored": 0, "surfaced": 0, "refreshed": 0}

        # Loaded once for the whole batch — these are small, user-scoped
        # config tables, and re-reading them per property would turn a
        # 100-property pass into 200 extra round-trips.
        searches = list(
            (await session.execute(select(SavedSearch).where(SavedSearch.is_active)))
            .scalars()
            .all()
        )
        profiles = list(
            (await session.execute(select(BuyerProfile).where(BuyerProfile.is_active)))
            .scalars()
            .all()
        )
        # `.tuples()` rather than `.all()` straight into `dict()`: a bare
        # `Row` is only `Sequence[Any]` to the type checker, so `dict(rows)`
        # infers `dict[Never, Never]` and every later lookup type-errors.
        # `.tuples()` is SQLAlchemy 2.0's typed view over the same rows and
        # carries the `tuple[UUID, str]` through at no runtime cost.
        user_external_ids: dict[uuid.UUID, str] = dict(
            (await session.execute(select(User.id, User.external_id))).tuples().all()
        )

        for pid in prop_ids:
            context = await _build_context(session, pid)
            if context is None:
                continue

            results = await _score_all_personas(session, pid, context, profiles)
            scored += 1

            for result in results:
                for lead_upsert in _leads_for(context, result, searches, profiles):
                    lead_id, was_new = await _upsert_lead(session, pid, result, lead_upsert)
                    if was_new:
                        surfaced += 1
                        emit_event(
                            session,
                            event_type="lead.surfaced",
                            aggregate_type="lead",
                            aggregate_id=str(lead_id),
                            payload=lead_surfaced_payload(
                                lead_id=lead_id,
                                property_id=pid,
                                user_external_id=user_external_ids.get(lead_upsert.user_id, ""),
                                persona=result.persona.value,
                                score=result.output.score,
                                scorer_version=result.scorer_version,
                            ),
                        )
                    else:
                        refreshed += 1

    logger.info("score.complete", scored=scored, surfaced=surfaced, refreshed=refreshed)
    return {"scored": scored, "surfaced": surfaced, "refreshed": refreshed}


# --------------------------------------------------------------------------
# Persona fan-out
# --------------------------------------------------------------------------


async def _score_all_personas(
    session: AsyncSession,
    property_id: uuid.UUID,
    context: PropertyContext,
    profiles: list[BuyerProfile],
) -> list[PersonaResult]:
    results: list[PersonaResult] = []

    flipper = FlipperScorer()
    wholesaler = WholesalerScorer()
    for persona, scorer in (
        (Persona.flipper, flipper),
        (Persona.wholesaler, wholesaler),
    ):
        output = scorer.score(context)
        score_id = await _upsert_score(session, property_id, persona, scorer.scorer_version, output)
        results.append(PersonaResult(persona, scorer.scorer_version, output, score_id))

    agent_result = await _score_buyers_agent(session, property_id, context, profiles)
    if agent_result is not None:
        results.append(agent_result)

    return results


async def _score_buyers_agent(
    session: AsyncSession,
    property_id: uuid.UUID,
    context: PropertyContext,
    profiles: list[BuyerProfile],
) -> PersonaResult | None:
    """Score the property against every active buyer profile.

    The matcher runs first and is authoritative on exclusion: a profile
    with a dealbreaker hit never reaches the scorer. Of the profiles that
    survive, the best-scoring one sets the persona score — an agent cares
    that the property fits *one* of their buyers, not the average.
    """
    if not profiles:
        return None

    matcher = BuyerMatcher()
    matches: list[BuyerMatch] = matcher.match(context.property, context.list_price_cents, profiles)
    if not matches:
        return None

    by_id = {str(p.id): p for p in profiles}
    scorer = BuyersAgentScorer()

    scored: list[tuple[str, ScoreOutput]] = []
    for match in matches:
        # Subscript rather than ``.get(...)`` with a skip. ``matcher.match``
        # only ever returns ``str(profile.id)`` for profiles out of the very
        # list ``by_id`` was built from, so a miss is impossible unless that
        # invariant breaks — and if it ever did, silently continuing would drop
        # the buyer from ``matched_profile_ids`` and hand the agent a lead that
        # does not say who to call. A ``KeyError`` says so out loud.
        profile = by_id[match.profile_id]
        scored.append(
            (
                match.profile_id,
                scorer.score_with_profile(
                    BuyersAgentContext(
                        property=context,
                        profile=profile,
                        must_have_matches=match.reasons,
                        deal_breaker_hits=match.dealbreakers,
                    )
                ),
            )
        )

    # Ties break on the profile id, for the same reason ``_buyers_agent_
    # thresholds`` is a fold: ``score_property_job`` loads buyer profiles with
    # no ``ORDER BY``. Two of an agent's buyers with identical criteria is an
    # ordinary thing — one profile copied for a similar client — and they score
    # identically, so "first maximum wins" hands the choice to Postgres row
    # order. The score would not move, but ``components`` and ``rationale``
    # would: the explanation the agent reads for why this house fits would
    # change from one nightly sweep to the next with nothing having happened.
    # The id is an arbitrary key on purpose. Among exact ties there is no
    # *right* buyer to pick, only a stable one.
    _, best = max(scored, key=lambda pair: (pair[1].score, pair[0]))

    score_id = await _upsert_score(
        session, property_id, Persona.buyers_agent, scorer.scorer_version, best
    )
    return PersonaResult(
        persona=Persona.buyers_agent,
        scorer_version=scorer.scorer_version,
        output=best,
        score_id=score_id,
        # Sorted at the producer so every consumer inherits a stable order:
        # these ids land verbatim in ``lead.rank_meta['matched_buyer_profiles']``,
        # and an unsorted list would rewrite that JSON on every sweep — a diff
        # in the audit trail, and a reshuffled call list in the UI, for a set
        # that never changed.
        matched_profile_ids=tuple(sorted(pid for pid, _ in scored)),
    )


# --------------------------------------------------------------------------
# Lead materialization
# --------------------------------------------------------------------------


class _LeadUpsert(NamedTuple):
    lead_id: uuid.UUID
    user_id: uuid.UUID
    saved_search_id: uuid.UUID | None
    rank_meta: dict[str, Any]


def _leads_for(
    context: PropertyContext,
    result: PersonaResult,
    searches: list[SavedSearch],
    profiles: list[BuyerProfile],
) -> list[_LeadUpsert]:
    if result.persona is Persona.buyers_agent:
        return _buyers_agent_leads(result, searches, profiles)
    return _saved_search_leads(context, result, searches)


def _saved_search_leads(
    context: PropertyContext,
    result: PersonaResult,
    searches: list[SavedSearch],
) -> list[_LeadUpsert]:
    out: list[_LeadUpsert] = []
    for search in searches:
        if search.persona != result.persona:
            continue
        if result.output.score < search.min_score:
            continue
        if not _property_in_scope(context.property, search):
            continue
        out.append(
            _LeadUpsert(
                lead_id=uuid.uuid4(),
                user_id=search.user_id,
                saved_search_id=search.id,
                rank_meta={
                    "scorer_version": result.scorer_version,
                    "matched_saved_search": str(search.id),
                },
            )
        )
    return out


def _buyers_agent_leads(
    result: PersonaResult,
    searches: list[SavedSearch],
    profiles: list[BuyerProfile],
) -> list[_LeadUpsert]:
    """One lead per agent, carrying every buyer profile that matched.

    ``lead`` is unique on (property, user, persona), so an agent with three
    matching buyers gets one row — the profile ids live in ``rank_meta`` so
    the frontend can still say *which* of their buyers this fits.
    """
    by_id = {str(p.id): p for p in profiles}
    thresholds = _buyers_agent_thresholds(searches)

    per_user: dict[uuid.UUID, list[str]] = {}
    for profile_id in result.matched_profile_ids:
        profile = by_id.get(profile_id)
        if profile is None:
            continue
        per_user.setdefault(profile.user_id, []).append(profile_id)

    out: list[_LeadUpsert] = []
    for user_id, profile_ids in per_user.items():
        threshold = thresholds.get(user_id, DEFAULT_BUYERS_AGENT_MIN_SCORE)
        if result.output.score < threshold:
            continue
        out.append(
            _LeadUpsert(
                lead_id=uuid.uuid4(),
                user_id=user_id,
                saved_search_id=None,
                rank_meta={
                    "scorer_version": result.scorer_version,
                    "matched_buyer_profiles": profile_ids,
                },
            )
        )
    return out


def _buyers_agent_thresholds(searches: list[SavedSearch]) -> dict[uuid.UUID, Decimal]:
    """Lowest buyers_agent ``min_score`` each user has configured.

    Written as a fold rather than the obvious dict comprehension
    (``{s.user_id: s.min_score for s in searches if ...}``) because the
    comprehension had a bug that no type checker or linter could see: with
    two buyers_agent searches for one user it keeps whichever was *iterated
    last*, and ``score_property_job`` selects saved searches with no ``ORDER
    BY``. Postgres row order therefore decided the threshold, so the same
    property could surface for an agent on one nightly run and not the next
    with nothing in the code having changed.

    Taking the minimum fixes both halves at once. It is order-independent, so
    the result is deterministic; and it is the correct semantics anyway — a
    saved search is a subscription, so if *any* of the agent's searches would
    accept this score, they asked to see it. Tightening one search must not
    silently mute another.

    Pinned by ``TestBuyersAgentThresholds::test_the_most_permissive_threshold_wins``,
    which asserts both input orderings; a regression to the comprehension
    passes one and fails the other.
    """
    thresholds: dict[uuid.UUID, Decimal] = {}
    for search in searches:
        if search.persona is not Persona.buyers_agent:
            continue
        current = thresholds.get(search.user_id)
        thresholds[search.user_id] = (
            search.min_score if current is None else min(current, search.min_score)
        )
    return thresholds


def _property_in_scope(prop: Property, search: SavedSearch) -> bool:
    """Does `prop` fall inside the search's geographic filters?

    An unset filter is not an empty filter: a saved search with no postal
    codes means "any postal code", not "no postal code will ever match".
    Stating each dimension positively keeps that distinction visible —
    the equivalent guard-clause form reads as a double negative and is
    where an off-by-one-`not` would hide.
    """
    postal_ok = not search.postal_codes or prop.postal_code in search.postal_codes
    city_ok = not search.cities or prop.city in search.cities
    return postal_ok and city_ok


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


async def _upsert_score(
    session: AsyncSession,
    property_id: uuid.UUID,
    persona: Persona,
    scorer_version: str,
    out: ScoreOutput,
) -> uuid.UUID:
    stmt = (
        pg_insert(Score)
        .values(
            id=uuid.uuid4(),
            property_id=property_id,
            persona=persona,
            scorer_version=scorer_version,
            score=out.score,
            confidence=out.confidence,
            components=out.components,
            rationale=out.rationale,
        )
        .on_conflict_do_update(
            index_elements=[Score.property_id, Score.persona, Score.scorer_version],
            set_={
                "score": out.score,
                "confidence": out.confidence,
                "components": out.components,
                "rationale": out.rationale,
                "updated_at": datetime.now(UTC),
            },
        )
        .returning(Score.id)
    )
    return (await session.execute(stmt)).scalar_one()


async def _upsert_lead(
    session: AsyncSession,
    property_id: uuid.UUID,
    result: PersonaResult,
    plan: _LeadUpsert,
) -> tuple[uuid.UUID, bool]:
    """Insert or refresh a lead. Returns ``(lead_id, was_inserted)``.

    We need the insert/update distinction because the outbox event must
    fire once — when the lead first appears — not on every nightly
    re-score. The usual folk trick is ``RETURNING (xmax = 0)``, but ``xmax``
    is a system column SQLAlchemy does not expose on the mapped table, and
    its value is not contractual under concurrent speculative insertion.

    Instead we lean on a property we already control: ``surfaced_at`` is
    written on the insert path and deliberately absent from the ``set_``
    clause, so an updated row keeps its original value. ``RETURNING`` on
    ``ON CONFLICT DO UPDATE`` yields the post-operation row, so comparing
    ``surfaced_at`` to the timestamp we just bound answers the question
    exactly. The comparison is evaluated server-side so no driver
    round-trip precision is involved.
    """
    now = datetime.now(UTC)
    stmt = (
        pg_insert(Lead)
        .values(
            id=plan.lead_id,
            property_id=property_id,
            user_id=plan.user_id,
            persona=result.persona,
            score_id=result.score_id,
            score_snapshot=result.output.score,
            status=LeadStatus.new,
            surfaced_at=now,
            saved_search_id=plan.saved_search_id,
            rank_meta=plan.rank_meta,
        )
        .on_conflict_do_update(
            index_elements=[Lead.property_id, Lead.user_id, Lead.persona],
            set_={
                # Refresh the valuation, preserve the human's state.
                "score_id": result.score_id,
                "score_snapshot": result.output.score,
                "rank_meta": plan.rank_meta,
                "updated_at": now,
            },
        )
        .returning(Lead.id, (Lead.surfaced_at == now).label("inserted"))
    )
    row = (await session.execute(stmt)).one()
    return row[0], bool(row[1])


# --------------------------------------------------------------------------
# Context assembly
# --------------------------------------------------------------------------


async def _resolve_property_ids(session: AsyncSession, property_id: str | None) -> list[uuid.UUID]:
    if property_id is not None:
        return [uuid.UUID(property_id)]
    stmt = (
        select(Property.id)
        .join(DealAnalysis, DealAnalysis.property_id == Property.id)
        .outerjoin(Score, Score.property_id == Property.id)
        .where(Score.id.is_(None))
        .limit(BATCH_LIMIT)
    )
    return [pid for (pid,) in (await session.execute(stmt)).all()]


async def _build_context(session: AsyncSession, property_id: uuid.UUID) -> PropertyContext | None:
    prop = (
        await session.execute(select(Property).where(Property.id == property_id))
    ).scalar_one_or_none()
    if prop is None:
        return None
    deal = (
        await session.execute(
            select(DealAnalysis)
            .where(DealAnalysis.property_id == property_id)
            .order_by(DealAnalysis.analysis_version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    snapshots = (
        (
            await session.execute(
                select(PropertySnapshot)
                .where(PropertySnapshot.property_id == property_id)
                .order_by(PropertySnapshot.observed_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .all()
    )
    signals = (
        (await session.execute(select(Signal).where(Signal.property_id == property_id)))
        .scalars()
        .all()
    )
    latest_snap = snapshots[0] if snapshots else None
    return PropertyContext(
        property=prop,
        signals=list(signals),
        avm_value_cents=(deal.avm_value_cents if deal else None),
        avm_low_cents=(deal.avm_low_cents if deal else None),
        avm_high_cents=(deal.avm_high_cents if deal else None),
        avm_confidence=(deal.avm_confidence if deal else None),
        arv_cents=(deal.arv_cents if deal else None),
        monthly_rent_cents=(deal.monthly_rent_cents if deal else None),
        rehab_low_cents=(deal.rehab_low_cents if deal else None),
        rehab_high_cents=(deal.rehab_high_cents if deal else None),
        overall_condition=(deal.overall_condition if deal else None),
        condition_confidence=(deal.condition_confidence if deal else None),
        red_flags=list((deal.red_flags if deal else []) or []),
        list_price_cents=(latest_snap.list_price_cents if latest_snap else None),
        days_on_market=(latest_snap.days_on_market if latest_snap else None),
    )
