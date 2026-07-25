"""Which users get a lead, and why — the pure half of `jobs/score.py`.

`score_property_job` is the biggest untested module in the repo, but most of
what it does is I/O. The decisions live in four pure functions, and those are
where a mistake is expensive and silent: a threshold read from the wrong place
does not raise, it just quietly stops surfacing deals to a paying user, or
starts surfacing junk. Nothing observes that except the customer.

So this file covers the four of them directly — `_property_in_scope`,
`_saved_search_leads`, `_buyers_agent_leads`, and the `_leads_for` dispatch —
with no session, no Postgres and no network. The persistence half (upsert
idempotency, the outbox firing exactly once) is proven separately in
`tests/integration/test_score_job.py`, because those claims are about what
Postgres does and cannot honestly be asserted against a mock.

Three behaviours pinned here are load-bearing and easy to break by accident:

1. **An unset filter means "any", not "none".** A saved search with no postal
   codes must match every postal code. The guard is written as a positive
   (`not search.postal_codes or ...`) precisely because the guard-clause form
   reads as a double negative — this is the test that fails if someone
   "simplifies" it back.
2. **The score threshold is inclusive.** `score < min_score` skips, so a score
   exactly equal to the threshold surfaces. Off-by-one here is invisible.
3. **A user with several buyer's-agent saved searches gets the most permissive
   threshold**, deterministically. See `TestBuyersAgentThresholds` — this was a
   real ordering bug this file caught.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest

from realty_lead_gen.jobs.score import (
    DEFAULT_BUYERS_AGENT_MIN_SCORE,
    PersonaResult,
    _buyers_agent_leads,
    _leads_for,
    _property_in_scope,
    _saved_search_leads,
)
from realty_lead_gen.models.buyer import BuyerProfile, BuyerReadiness, SavedSearch
from realty_lead_gen.models.property import Property, PropertyType
from realty_lead_gen.models.score import Persona
from realty_lead_gen.scoring.base import PropertyContext, ScoreOutput

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------
# Builders
#
# These construct mapped classes without a session on purpose. A SQLAlchemy
# declarative instance is an ordinary Python object until something flushes
# it, and the functions under test only read attributes — so this stays a
# unit test rather than quietly becoming an integration one.
# --------------------------------------------------------------------------


def _prop(**overrides: Any) -> Property:
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "address_hash": "d" * 64,
        "street_name": "MAIN ST",
        "city": "AUSTIN",
        "state": "TX",
        "postal_code": "78701",
        "property_type": PropertyType.single_family,
        "bedrooms": 3,
        "bathrooms": Decimal("2"),
        "living_area_sqft": 1800,
        "attributes": {},
    }
    fields.update(overrides)
    return Property(**fields)


def _ctx(prop: Property | None = None) -> PropertyContext:
    """A context with every enrichment field empty.

    None of these functions read the valuation fields — they read the score
    that a scorer already produced — so leaving them `None` states that
    dependency rather than implying one that does not exist.
    """
    return PropertyContext(
        property=prop if prop is not None else _prop(),
        signals=[],
        avm_value_cents=None,
        avm_low_cents=None,
        avm_high_cents=None,
        avm_confidence=None,
        arv_cents=None,
        monthly_rent_cents=None,
        rehab_low_cents=None,
        rehab_high_cents=None,
        overall_condition=None,
        condition_confidence=None,
        red_flags=[],
        list_price_cents=300_000_00,
        days_on_market=None,
    )


def _search(
    *,
    user_id: uuid.UUID | None = None,
    persona: Persona = Persona.flipper,
    min_score: str = "0.50",
    postal_codes: list[str] | None = None,
    cities: list[str] | None = None,
) -> SavedSearch:
    return SavedSearch(
        id=uuid.uuid4(),
        user_id=user_id if user_id is not None else uuid.uuid4(),
        name="test search",
        persona=persona,
        postal_codes=[] if postal_codes is None else postal_codes,
        cities=[] if cities is None else cities,
        regions=[],
        min_score=Decimal(min_score),
        criteria={},
        is_active=True,
    )


def _profile(user_id: uuid.UUID) -> BuyerProfile:
    return BuyerProfile(
        id=uuid.uuid4(),
        user_id=user_id,
        display_name="Buyer",
        target_cities=[],
        target_postal_codes=[],
        max_price_cents=None,
        min_price_cents=None,
        min_bedrooms=None,
        min_bathrooms=None,
        min_living_area_sqft=None,
        max_living_area_sqft=None,
        property_types=[],
        must_haves=[],
        nice_to_haves=[],
        deal_breakers=[],
        readiness=BuyerReadiness.unknown,
        is_active=True,
    )


def _result(
    *,
    persona: Persona = Persona.flipper,
    score: str = "0.75",
    matched_profile_ids: tuple[str, ...] = (),
) -> PersonaResult:
    return PersonaResult(
        persona=persona,
        scorer_version=f"{persona.value}_v1",
        output=ScoreOutput(score=Decimal(score), confidence=Decimal("0.800")),
        score_id=uuid.uuid4(),
        matched_profile_ids=matched_profile_ids,
    )


# --------------------------------------------------------------------------
# Geographic scope
# --------------------------------------------------------------------------


class TestPropertyInScope:
    """`_property_in_scope` — the "unset means any" rule, stated once."""

    def test_a_search_with_no_geography_matches_everything(self) -> None:
        """The whole reason the guard is written positively.

        An empty list is falsy, so the naive reading — "the property's zip is
        not in `[]`, therefore no match" — is wrong and would make every
        nationwide saved search return nothing. That failure mode is silent:
        the user just sees an empty feed.
        """
        assert _property_in_scope(_prop(), _search()) is True

    @pytest.mark.parametrize(
        ("postal_codes", "cities", "expected"),
        [
            (["78701"], [], True),
            (["78702"], [], False),
            ([], ["AUSTIN"], True),
            ([], ["DALLAS"], False),
            # Both set and both satisfied.
            (["78701"], ["AUSTIN"], True),
            # Both set, one satisfied — the dimensions are conjunctive, so a
            # zip hit does not rescue a city miss.
            (["78701"], ["DALLAS"], False),
            (["78702"], ["AUSTIN"], False),
        ],
    )
    def test_filters_are_conjunctive_across_dimensions(
        self,
        postal_codes: list[str],
        cities: list[str],
        expected: bool,
    ) -> None:
        search = _search(postal_codes=postal_codes, cities=cities)
        assert _property_in_scope(_prop(), search) is expected

    def test_matching_is_exact_not_normalized(self) -> None:
        """Case sensitivity is a real contract, not an oversight.

        Cities are stored upper-cased by the normalization step in
        `pipeline/normalize.py`, and a saved search stores whatever the API
        was handed. Asserting the mismatch here documents that the API layer
        owns the casing, so if that ever moves this test is the thing that
        says where the responsibility used to live.
        """
        assert _property_in_scope(_prop(), _search(cities=["Austin"])) is False


# --------------------------------------------------------------------------
# Saved-search leads (flipper / wholesaler)
# --------------------------------------------------------------------------


class TestSavedSearchLeads:
    def test_a_matching_search_produces_one_lead_carrying_its_provenance(self) -> None:
        search = _search(postal_codes=["78701"], min_score="0.50")
        result = _result(score="0.75")

        leads = _saved_search_leads(_ctx(), result, [search])

        assert len(leads) == 1
        lead = leads[0]
        assert lead.user_id == search.user_id
        assert lead.saved_search_id == search.id
        # `rank_meta` is the "why did this appear?" audit the API exposes;
        # both keys are read by the frontend, so their names are contract.
        assert lead.rank_meta == {
            "scorer_version": "flipper_v1",
            "matched_saved_search": str(search.id),
        }

    def test_a_search_for_a_different_persona_is_ignored(self) -> None:
        """One property scores for every persona; a search subscribes to one.

        Without this filter a wholesaler's saved search would receive the
        flipper score — a number computed by different arithmetic against a
        different threshold, which is worse than no lead at all.
        """
        search = _search(persona=Persona.wholesaler)
        assert _saved_search_leads(_ctx(), _result(persona=Persona.flipper), [search]) == []

    @pytest.mark.parametrize(
        ("score", "min_score", "surfaced"),
        [
            ("0.7500", "0.5000", True),
            # Exactly at the threshold surfaces: the guard is `score <
            # min_score`, so the boundary is inclusive. A user who sets 0.60
            # gets the 0.60 deals.
            ("0.6000", "0.6000", True),
            # One unit of the column's scale below. `Numeric(5, 4)` means
            # 0.0001 is the smallest representable step, so this is the
            # tightest possible statement of where the line sits.
            ("0.5999", "0.6000", False),
            ("0.1000", "0.6000", False),
        ],
    )
    def test_the_threshold_is_inclusive(self, score: str, min_score: str, surfaced: bool) -> None:
        leads = _saved_search_leads(
            _ctx(),
            _result(score=score),
            [_search(min_score=min_score)],
        )
        assert bool(leads) is surfaced

    def test_an_out_of_scope_property_is_skipped_even_with_a_high_score(self) -> None:
        search = _search(postal_codes=["78702"], min_score="0.10")
        assert _saved_search_leads(_ctx(), _result(score="0.99"), [search]) == []

    def test_every_matching_search_gets_its_own_lead(self) -> None:
        """Two users watching the same zip both get told.

        Also pins that `lead_id` is minted per upsert rather than shared —
        `lead` is unique on (property, user, persona), so two users must
        arrive with two distinct ids or the second insert would collide with
        the first instead of creating a row.
        """
        alice, bob = uuid.uuid4(), uuid.uuid4()
        searches = [
            _search(user_id=alice, postal_codes=["78701"]),
            _search(user_id=bob, cities=["AUSTIN"]),
        ]

        leads = _saved_search_leads(_ctx(), _result(), searches)

        assert {lead.user_id for lead in leads} == {alice, bob}
        assert len({lead.lead_id for lead in leads}) == 2

    def test_one_user_with_two_overlapping_searches_gets_two_leads(self) -> None:
        """Observed behaviour, recorded rather than endorsed.

        Both searches match, so both produce a `_LeadUpsert` for the same
        (property, user, persona) — and the unique index means the second
        upsert updates the row the first inserted. The net effect is one lead
        whose `rank_meta.matched_saved_search` names whichever ran last, and
        `surfaced` is counted once because only the first insert reports
        `inserted`. That is benign today; it is written down because if
        `rank_meta` ever needs to list *all* matching searches, this is the
        function that has to change and this is the test that will fail.
        """
        user = uuid.uuid4()
        searches = [
            _search(user_id=user, postal_codes=["78701"]),
            _search(user_id=user, cities=["AUSTIN"]),
        ]

        leads = _saved_search_leads(_ctx(), _result(), searches)

        assert len(leads) == 2
        assert {lead.user_id for lead in leads} == {user}

    def test_no_searches_means_no_leads(self) -> None:
        """A scored property with nobody subscribed is not an error.

        The nightly sweep scores everything it ingested; most of it matches
        nobody. This has to be an empty list, not an exception.
        """
        assert _saved_search_leads(_ctx(), _result(), []) == []


# --------------------------------------------------------------------------
# Buyer's-agent leads
# --------------------------------------------------------------------------


class TestBuyersAgentLeads:
    def test_three_matching_buyers_collapse_into_one_lead_for_their_agent(self) -> None:
        """`lead` is unique on (property, user, persona), so it has to.

        The profile ids move into `rank_meta` so the frontend can still say
        *which* of the agent's buyers this fits — the information is
        preserved, just not as extra rows.
        """
        agent = uuid.uuid4()
        profiles = [_profile(agent), _profile(agent), _profile(agent)]
        ids = tuple(str(p.id) for p in profiles)

        leads = _buyers_agent_leads(
            _result(persona=Persona.buyers_agent, score="0.90", matched_profile_ids=ids),
            [],
            profiles,
        )

        assert len(leads) == 1
        assert leads[0].user_id == agent
        assert leads[0].rank_meta["matched_buyer_profiles"] == list(ids)
        assert leads[0].rank_meta["scorer_version"] == "buyers_agent_v1"

    def test_two_agents_with_matching_buyers_each_get_a_lead(self) -> None:
        alice, bob = uuid.uuid4(), uuid.uuid4()
        profiles = [_profile(alice), _profile(bob)]
        ids = tuple(str(p.id) for p in profiles)

        leads = _buyers_agent_leads(
            _result(persona=Persona.buyers_agent, score="0.90", matched_profile_ids=ids),
            [],
            profiles,
        )

        by_user = {lead.user_id: lead for lead in leads}
        assert set(by_user) == {alice, bob}
        assert by_user[alice].rank_meta["matched_buyer_profiles"] == [str(profiles[0].id)]
        assert by_user[bob].rank_meta["matched_buyer_profiles"] == [str(profiles[1].id)]

    def test_a_buyers_agent_lead_carries_no_saved_search_id(self) -> None:
        """The provenance is the buyer profile, not a search.

        A buyer profile *is* the search for this persona, so attributing the
        lead to a `saved_search` row — even the one that supplied the
        threshold — would misreport why it appeared.
        """
        agent = uuid.uuid4()
        profile = _profile(agent)
        leads = _buyers_agent_leads(
            _result(
                persona=Persona.buyers_agent,
                score="0.90",
                matched_profile_ids=(str(profile.id),),
            ),
            [_search(user_id=agent, persona=Persona.buyers_agent, min_score="0.10")],
            [profile],
        )
        assert leads[0].saved_search_id is None

    def test_a_matched_id_with_no_surviving_profile_is_dropped(self) -> None:
        """Defensive, and the defence is the right one.

        `matched_profile_ids` is captured earlier in the pass than this
        lookup, so a profile deactivated in between is a live race. Dropping
        it means the agent does not get a lead for a buyer they no longer
        represent; raising would abandon the whole batch over one stale row.
        """
        agent = uuid.uuid4()
        profile = _profile(agent)
        leads = _buyers_agent_leads(
            _result(
                persona=Persona.buyers_agent,
                score="0.90",
                matched_profile_ids=(str(profile.id), str(uuid.uuid4())),
            ),
            [],
            [profile],
        )
        assert len(leads) == 1
        assert leads[0].rank_meta["matched_buyer_profiles"] == [str(profile.id)]

    def test_no_matched_profiles_means_no_leads(self) -> None:
        assert _buyers_agent_leads(_result(persona=Persona.buyers_agent), [], []) == []


class TestBuyersAgentThresholds:
    """Where the buyer's-agent score threshold comes from.

    This persona is the odd one out: a flipper's threshold is on the saved
    search that matched, but a buyer's-agent lead is produced by profiles, so
    the threshold has to be looked up by user instead. That lookup is the
    fiddly part, and the class exists because writing it out found a bug —
    see `test_the_most_permissive_threshold_wins`.
    """

    @staticmethod
    def _leads_at(score: str, searches: list[SavedSearch], agent: uuid.UUID) -> int:
        profile = _profile(agent)
        return len(
            _buyers_agent_leads(
                _result(
                    persona=Persona.buyers_agent,
                    score=score,
                    matched_profile_ids=(str(profile.id),),
                ),
                searches,
                [profile],
            )
        )

    def test_the_users_own_saved_search_sets_the_threshold(self) -> None:
        agent = uuid.uuid4()
        strict = [_search(user_id=agent, persona=Persona.buyers_agent, min_score="0.90")]
        assert self._leads_at("0.95", strict, agent) == 1
        assert self._leads_at("0.80", strict, agent) == 0

    def test_another_users_saved_search_does_not_apply(self) -> None:
        """The threshold dict is keyed by user, and this is what proves it.

        A stranger's strict search must not suppress this agent's lead — if
        the keying were wrong that would look like a mysteriously empty feed
        for whichever agent happened to be scored second.
        """
        agent = uuid.uuid4()
        stranger = [_search(user_id=uuid.uuid4(), persona=Persona.buyers_agent, min_score="0.99")]
        assert self._leads_at("0.60", stranger, agent) == 1

    def test_a_search_for_another_persona_does_not_apply(self) -> None:
        """Same user, wrong persona.

        An agent who also flips houses has a flipper search with its own
        threshold. Reading it here would apply flipper economics to a buyer
        fit score — two numbers on the same 0..1 scale that mean nothing
        alike. A 0.99 flipper threshold must leave the 0.60 buyer lead alone.
        """
        agent = uuid.uuid4()
        wrong_persona = [_search(user_id=agent, persona=Persona.flipper, min_score="0.99")]
        assert self._leads_at("0.60", wrong_persona, agent) == 1

    def test_the_default_applies_when_the_user_has_no_saved_search(self) -> None:
        """A buyer profile is itself the search, so there may be no search row.

        The boundary is asserted against the constant rather than a literal:
        the claim under test is that the comparison is inclusive (`score <
        threshold` skips), not that the default happens to be 0.55 today.
        """
        agent = uuid.uuid4()
        at = DEFAULT_BUYERS_AGENT_MIN_SCORE
        just_under = at - Decimal("0.0001")
        assert self._leads_at(str(at), [], agent) == 1
        assert self._leads_at(str(just_under), [], agent) == 0

    def test_the_most_permissive_threshold_wins(self) -> None:
        """Two buyer's-agent searches for one user: the lower bar applies.

        This is the bug the file caught. The lookup was a dict comprehension
        over the searches, so with two rows for the same user the *last one
        iterated* silently won — and `score_property_job` selects saved
        searches with no `ORDER BY`, which means Postgres row order decided
        the threshold. The same property could surface for an agent on one
        run and not the next, with nothing in the code changing.

        Taking the minimum fixes both halves at once. It is order-independent,
        so the result is deterministic; and it is the correct semantics anyway
        — a saved search is a subscription, so if *any* of the agent's
        searches would accept this score, they asked to see it.

        Asserting both orderings is the point: a dict-comprehension
        regression passes one and fails the other.
        """
        agent = uuid.uuid4()
        lenient = _search(user_id=agent, persona=Persona.buyers_agent, min_score="0.30")
        strict = _search(user_id=agent, persona=Persona.buyers_agent, min_score="0.90")

        assert self._leads_at("0.50", [lenient, strict], agent) == 1
        assert self._leads_at("0.50", [strict, lenient], agent) == 1
        # Below even the lenient bar, nothing surfaces in either order.
        assert self._leads_at("0.20", [lenient, strict], agent) == 0
        assert self._leads_at("0.20", [strict, lenient], agent) == 0


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------


class TestLeadsForDispatch:
    """`_leads_for` routes by persona. Both branches, and why they differ."""

    def test_buyers_agent_routes_to_the_profile_path(self) -> None:
        """And therefore ignores the saved search's geography.

        The search below is scoped to a zip the property is not in, and the
        lead is produced anyway. That is correct, not a leak: the buyer
        profile carries its own `target_postal_codes` and `BuyerMatcher`
        already applied them upstream, so re-filtering on an unrelated
        search's geography would drop matches the buyer explicitly wanted.
        """
        agent = uuid.uuid4()
        profile = _profile(agent)
        out_of_scope = _search(
            user_id=agent,
            persona=Persona.buyers_agent,
            min_score="0.10",
            postal_codes=["99999"],
        )

        leads = _leads_for(
            _ctx(),
            _result(
                persona=Persona.buyers_agent,
                score="0.80",
                matched_profile_ids=(str(profile.id),),
            ),
            [out_of_scope],
            [profile],
        )

        assert len(leads) == 1
        assert "matched_buyer_profiles" in leads[0].rank_meta
        assert leads[0].saved_search_id is None

    @pytest.mark.parametrize("persona", [Persona.flipper, Persona.wholesaler])
    def test_investor_personas_route_to_the_saved_search_path(self, persona: Persona) -> None:
        search = _search(user_id=uuid.uuid4(), persona=persona, postal_codes=["78701"])

        leads = _leads_for(_ctx(), _result(persona=persona), [search], [])

        assert len(leads) == 1
        assert leads[0].saved_search_id == search.id
        assert "matched_saved_search" in leads[0].rank_meta

    def test_the_investor_path_does_not_consult_buyer_profiles(self) -> None:
        """Buyer profiles are irrelevant to a flipper lead, and vice versa.

        Passing a fully populated profile list and getting nothing is the
        assertion that the two paths do not bleed: a flipper does not acquire
        leads because somebody's buyer happens to like the house.
        """
        agent = uuid.uuid4()
        assert _leads_for(_ctx(), _result(persona=Persona.flipper), [], [_profile(agent)]) == []
