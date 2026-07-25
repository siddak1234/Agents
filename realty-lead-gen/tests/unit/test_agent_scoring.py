"""Buyer's-agent scorer — the arithmetic, the contract, and one known defect.

`scoring/agent.py` names this file in a comment, because the weight-sum
assertion below is load-bearing: `score_with_profile` sums four weighted
components and calls the result a score in [0, 1]. `clamp_unit` *hides* a
weight-sum bug rather than exposing it — if the weights summed to 1.3, a
good property would silently pin at 1.0000 and stop being distinguishable
from a merely decent one, while every downstream threshold
(`SavedSearch.min_score`, the API's `min_score` filter) quietly changed
meaning. Nothing else in the codebase would fail.

The `components` dict is asserted on directly and not only through the
composite, because it is persisted verbatim into `Score.components` (JSONB)
and served through `ScoreDTO`. Its keys are a wire contract with the
frontend.

One test here — `test_unknown_bed_bath_is_punished_but_unknown_sqft_is_not`
— pins behaviour the module docstring used to describe inaccurately. It is
an inconsistency, not a design, and it is pinned rather than fixed so that
changing it is a product decision with a failing test attached.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from realty_lead_gen.models.buyer import BuyerProfile, BuyerReadiness
from realty_lead_gen.models.property import Property, PropertyType
from realty_lead_gen.scoring.agent import (
    _CONFIDENCE_WITH_CRITERIA,
    _CONFIDENCE_WITHOUT_CRITERIA,
    _DOM_WEIGHT,
    _DOM_ZERO_AT,
    _HARD_WEIGHT,
    _MUST_HAVE_WEIGHT,
    _NEUTRAL,
    _PRICE_POSITION_WEIGHT,
    BuyersAgentContext,
    BuyersAgentScorer,
)
from realty_lead_gen.scoring.base import PropertyContext, ScoreOutput, clamp_unit

pytestmark = pytest.mark.unit


def _prop(**kw) -> Property:
    d: dict = {
        "id": uuid.uuid4(),
        "address_hash": "a" * 64,
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
    d.update(kw)
    return Property(**d)


def _profile(**kw) -> BuyerProfile:
    """A profile with *nothing* configured, so each test opts in explicitly.

    Deliberately not the "realistic buyer" fixture the matcher tests use:
    here the denominator of the hard-criteria ratio is itself under test, so
    a default that quietly contributed six criteria would make every ratio
    assertion depend on the fixture rather than on the case.
    """
    d: dict = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "display_name": "Test",
        "target_cities": [],
        "target_postal_codes": [],
        "max_price_cents": None,
        "min_price_cents": None,
        "min_bedrooms": None,
        "min_bathrooms": None,
        "min_living_area_sqft": None,
        "max_living_area_sqft": None,
        "property_types": [],
        "must_haves": [],
        "nice_to_haves": [],
        "deal_breakers": [],
        "readiness": BuyerReadiness.pre_approved,
        "is_active": True,
    }
    d.update(kw)
    return BuyerProfile(**d)


def _pctx(**kw) -> PropertyContext:
    d: dict = {
        "property": _prop(),
        "signals": [],
        "avm_value_cents": None,
        "avm_low_cents": None,
        "avm_high_cents": None,
        "avm_confidence": None,
        "arv_cents": None,
        "monthly_rent_cents": None,
        "rehab_low_cents": None,
        "rehab_high_cents": None,
        "overall_condition": None,
        "condition_confidence": None,
        "red_flags": [],
        "list_price_cents": None,
        "days_on_market": None,
    }
    d.update(kw)
    return PropertyContext(**d)


def _score(
    *,
    profile: BuyerProfile | None = None,
    pctx: PropertyContext | None = None,
    must_have_matches: list[str] | None = None,
    deal_breaker_hits: list[str] | None = None,
) -> ScoreOutput:
    return BuyersAgentScorer().score_with_profile(
        BuyersAgentContext(
            property=_pctx() if pctx is None else pctx,
            profile=profile,
            must_have_matches=must_have_matches or [],
            deal_breaker_hits=deal_breaker_hits or [],
        )
    )


def _value(out: ScoreOutput, component: str) -> float:
    return float(out.components[component]["value"])


# --- the invariant the module comment points at ------------------------------


def test_component_weights_sum_to_exactly_one() -> None:
    """`Decimal`, not `float` — `0.55 + 0.20 + 0.15 + 0.10 != 1.0` in binary.

    That is why the weights are `Decimal` constants in the first place, and
    asserting on them as floats here would let the test pass while proving
    the wrong thing.
    """
    total = _HARD_WEIGHT + _MUST_HAVE_WEIGHT + _PRICE_POSITION_WEIGHT + _DOM_WEIGHT
    assert total == Decimal("1")


def test_emitted_weights_match_the_constants_and_also_sum_to_one() -> None:
    """The persisted `components` must explain the score it accompanies.

    The composite is summed from these emitted floats, so a weight edited
    in the constant but not in the dict (or the reverse) would produce a
    score its own stored explanation does not add up to.
    """
    emitted = {k: Decimal(str(v["weight"])) for k, v in _score().components.items()}
    assert emitted == {
        "hard_criteria": _HARD_WEIGHT,
        "must_haves": _MUST_HAVE_WEIGHT,
        "price_position": _PRICE_POSITION_WEIGHT,
        "dom_freshness": _DOM_WEIGHT,
    }
    assert sum(emitted.values()) == Decimal("1")


def test_composite_is_the_weighted_sum_of_the_emitted_components() -> None:
    out = _score(
        profile=_profile(max_price_cents=500_000_00, min_bedrooms=2, must_haves=["garage"]),
        must_have_matches=["garage"],
        pctx=_pctx(list_price_cents=400_000_00, avm_value_cents=460_000_00, days_on_market=20),
    )
    expected = sum(
        (Decimal(str(c["value"])) * Decimal(str(c["weight"])) for c in out.components.values()),
        Decimal("0"),
    )
    assert out.score == clamp_unit(expected)


# --- deal-breakers -----------------------------------------------------------


def test_deal_breakers_short_circuit_to_zero_with_high_confidence() -> None:
    """A deal-breaker is a disqualification, not a low score.

    High confidence because it took no inference: the buyer named the
    condition and the property has it. The `components` payload collapses
    to just the hits — asserted because a frontend reading
    `components["hard_criteria"]` has to be able to tell this case apart
    from a genuinely bad match rather than crash on a missing key.
    """
    out = _score(
        profile=_profile(max_price_cents=500_000_00),
        deal_breaker_hits=["hoa", "flood zone"],
        pctx=_pctx(list_price_cents=100_000_00, avm_value_cents=400_000_00, days_on_market=0),
    )
    assert out.score == Decimal("0.0000")
    assert out.confidence == Decimal("0.900")
    assert out.components == {"deal_breakers": {"hits": ["hoa", "flood zone"]}}
    assert "hoa" in (out.rationale or "")


def test_deal_breakers_beat_an_otherwise_perfect_property() -> None:
    profile = _profile(max_price_cents=500_000_00, target_cities=["AUSTIN"])
    pctx = _pctx(list_price_cents=300_000_00, avm_value_cents=400_000_00, days_on_market=0)
    clean = _score(profile=profile, pctx=pctx)
    flagged = _score(profile=profile, pctx=pctx, deal_breaker_hits=["structural"])
    assert clean.score > Decimal("0.7")
    assert flagged.score == Decimal("0.0000")


# --- hard criteria: the denominator rule -------------------------------------


def test_unconfigured_criteria_do_not_enter_the_denominator() -> None:
    """A buyer who stated one requirement is judged on one requirement.

    If unstated criteria counted as misses, every sparse profile would be
    capped near zero and the score would rank buyers by how much of the
    form they filled in.
    """
    hard = _score(profile=_profile(target_cities=["AUSTIN"])).components["hard_criteria"]
    assert (hard["hits"], hard["max"]) == (1, 1)
    assert hard["value"] == 1.0


def test_price_bounds_are_skipped_when_the_listing_has_no_price() -> None:
    """Off-market and pre-foreclosure records legitimately have no price.

    Counting the buyer's budget as a miss there would bury exactly the
    inventory the off-market sources exist to surface.
    """
    hard = _score(profile=_profile(max_price_cents=1, min_price_cents=1)).components[
        "hard_criteria"
    ]
    assert (hard["hits"], hard["max"]) == (0, 0)
    assert hard["value"] == float(_NEUTRAL)
    # `notes` is populated only for a bound that was actually evaluated.
    assert hard["notes"] == {}


def test_price_bounds_are_recorded_in_notes_when_evaluated() -> None:
    """The two an agent most often wants spelled out ("why is this here?")."""
    out = _score(
        profile=_profile(max_price_cents=400_000_00, min_price_cents=200_000_00),
        pctx=_pctx(list_price_cents=500_000_00),
    )
    hard = out.components["hard_criteria"]
    assert hard["notes"] == {"max_price": False, "min_price": True}
    assert (hard["hits"], hard["max"]) == (1, 2)


def test_unknown_bed_bath_is_punished_but_unknown_sqft_is_not() -> None:
    """Pinning a real inconsistency, deliberately, rather than fixing it.

    All three of `bedrooms`, `bathrooms` and `living_area_sqft` are
    nullable on `Property`, and the scorer treats the third differently
    from the first two: an unrecorded square footage drops the criterion
    from numerator *and* denominator, while an unrecorded bedroom or
    bathroom count is scored as an outright miss with the denominator still
    incremented. So a listing whose bed/bath fields the source never
    populated is ranked as if it had failed the buyer's requirements — the
    scorer punishing a gap in *our* data, which is precisely what a ranker
    is not supposed to do.

    This predates the decomposition of this module (the refactor was
    verified byte-identical against the previous implementation), so it is
    pinned here as behaviour rather than quietly changed. Unifying the rule
    is a product decision; when it is made, this is the test that should
    fail.
    """
    unknown_beds = _score(
        profile=_profile(min_bedrooms=3), pctx=_pctx(property=_prop(bedrooms=None))
    )
    hard = unknown_beds.components["hard_criteria"]
    assert (hard["hits"], hard["max"]) == (0, 1), "unknown bedrooms counted as a miss"

    unknown_baths = _score(
        profile=_profile(min_bathrooms=Decimal("2")), pctx=_pctx(property=_prop(bathrooms=None))
    )
    hard = unknown_baths.components["hard_criteria"]
    assert (hard["hits"], hard["max"]) == (0, 1), "unknown bathrooms counted as a miss"

    unknown_sqft = _score(
        profile=_profile(min_living_area_sqft=1500, max_living_area_sqft=4000),
        pctx=_pctx(property=_prop(living_area_sqft=None)),
    )
    hard = unknown_sqft.components["hard_criteria"]
    assert (hard["hits"], hard["max"]) == (0, 0), "unknown sqft dropped from both sides"


def test_geo_and_type_criteria_are_always_evaluable() -> None:
    """`city`, `postal_code` and `property_type` are NOT NULL on `Property`.

    So unlike bed/bath/sqft there is no unknown case to disagree about,
    which is why the inconsistency above is confined to two fields.
    """
    out = _score(
        profile=_profile(
            target_cities=["DALLAS"],
            target_postal_codes=["78701"],
            property_types=["condo"],
        )
    )
    hard = out.components["hard_criteria"]
    assert (hard["hits"], hard["max"]) == (1, 3)


# --- must-haves --------------------------------------------------------------


def test_must_haves_are_a_ratio_over_what_the_buyer_stated() -> None:
    out = _score(
        profile=_profile(must_haves=["garage", "pool", "yard"]),
        must_have_matches=["garage", "pool"],
    )
    assert _value(out, "must_haves") == pytest.approx(2 / 3, abs=1e-4)
    assert out.components["must_haves"]["matched"] == ["garage", "pool"]


def test_no_stated_must_haves_is_neutral_not_perfect() -> None:
    """An empty requirement list is unexamined, not 100% satisfied.

    `0/0 -> 1.0` would hand every buyer who skipped the free-text box a
    fifth of the composite for free.
    """
    assert _value(_score(profile=_profile(must_haves=[])), "must_haves") == float(_NEUTRAL)
    assert _value(_score(profile=None), "must_haves") == float(_NEUTRAL)


def test_more_matches_than_requirements_clamps_to_one() -> None:
    """Defensive: the upstream matcher is not trusted to have deduped."""
    out = _score(profile=_profile(must_haves=["garage"]), must_have_matches=["garage", "garage"])
    assert _value(out, "must_haves") == 1.0


# --- price position ----------------------------------------------------------


@pytest.mark.parametrize(
    ("list_price", "avm", "expected"),
    [
        (400_000_00, 400_000_00, 0.0),  # at the model: no edge
        (360_000_00, 400_000_00, 0.2),  # 10% under: a good find, scored small
        (350_000_00, 400_000_00, 0.25),  # 12.5% under
        (300_000_00, 400_000_00, 0.5),  # 25% under: only half the component
        (200_000_00, 400_000_00, 1.0),  # 50% under: saturation point
        (100_000_00, 400_000_00, 1.0),  # further under: clamped, not >1
        (500_000_00, 400_000_00, 0.0),  # over the model: clamped, not negative
    ],
)
def test_price_position_curve(list_price: int, avm: int, expected: float) -> None:
    """`2 - 2 * (list / avm)`: zero at the AVM, saturating at *half* of it.

    This test found a wrong comment. The docstring on
    `_price_position_value` claimed the component saturated at 25% under
    the AVM; the arithmetic says 25% under scores 0.5 and saturation needs
    a 50% discount. The formula predates the refactor of this module and
    was verified unchanged by it, so the comment was corrected rather than
    the code — but the practical reading matters: a listing 10% under the
    model earns 0.2 here, which after the 0.15 weight is 3% of the
    composite. `price_position` is a distressed-pricing detector, not a
    general "good deal" signal, and the table above is the evidence.
    """
    out = _score(pctx=_pctx(list_price_cents=list_price, avm_value_cents=avm))
    assert _value(out, "price_position") == pytest.approx(expected, abs=1e-4)


def test_price_position_is_neutral_without_an_avm() -> None:
    """Missing AVM coverage must read as "no signal", not "bad".

    Scoring it down would systematically bury rural and new-construction
    inventory, which is where AVM coverage is thinnest.
    """
    for pctx in (
        _pctx(list_price_cents=400_000_00, avm_value_cents=None),
        _pctx(list_price_cents=None, avm_value_cents=400_000_00),
        # Falsy, not just `None`: a zero on either side takes the same
        # branch rather than dividing.
        _pctx(list_price_cents=0, avm_value_cents=400_000_00),
        _pctx(list_price_cents=400_000_00, avm_value_cents=0),
    ):
        assert _value(_score(pctx=pctx), "price_position") == float(_NEUTRAL)


# --- days on market ----------------------------------------------------------


@pytest.mark.parametrize(
    ("dom", "expected"),
    [(0, 1.0), (15, 0.75), (30, 0.5), (60, 0.0), (120, 0.0)],
)
def test_dom_freshness_decays_linearly_to_zero_at_the_cutoff(dom: int, expected: float) -> None:
    out = _score(pctx=_pctx(days_on_market=dom))
    assert _value(out, "dom_freshness") == pytest.approx(expected, abs=1e-4)
    assert out.components["dom_freshness"]["dom"] == dom


def test_dom_cutoff_constant_matches_the_curve() -> None:
    out = _score(pctx=_pctx(days_on_market=int(_DOM_ZERO_AT)))
    assert _value(out, "dom_freshness") == 0.0


def test_unknown_dom_is_treated_as_brand_new() -> None:
    """`days_on_market or 0` — worth knowing, and not the neutral rule.

    Every other component answers "no evidence" with 0.5. This one hands an
    unknown listing the full freshness bonus, so a source that never
    populates DOM gets a systematic 0.10 lift over one that does. That is a
    tenth of the composite rather than a correctness bug, and it is pinned
    here so the asymmetry stays visible if the weight ever grows.
    """
    out = _score(pctx=_pctx(days_on_market=None))
    assert _value(out, "dom_freshness") == 1.0
    assert out.components["dom_freshness"]["dom"] == 0


# --- confidence and the no-profile path --------------------------------------


def test_confidence_reflects_whether_anything_checkable_existed() -> None:
    with_criteria = _score(profile=_profile(target_cities=["AUSTIN"]))
    assert with_criteria.confidence == _CONFIDENCE_WITH_CRITERIA
    # A profile that configured nothing is indistinguishable from no profile.
    assert _score(profile=_profile()).confidence == _CONFIDENCE_WITHOUT_CRITERIA
    assert _score(profile=None).confidence == _CONFIDENCE_WITHOUT_CRITERIA


def test_scoring_without_a_profile_still_produces_a_full_component_set() -> None:
    """The generic "market interest" path is not a degenerate one.

    `price_position` and `dom_freshness` need no buyer at all, so a
    profile-less score is still informative — and the frontend gets the
    same four keys either way.
    """
    out = _score(
        profile=None,
        pctx=_pctx(list_price_cents=300_000_00, avm_value_cents=400_000_00, days_on_market=0),
    )
    assert set(out.components) == {
        "hard_criteria",
        "must_haves",
        "price_position",
        "dom_freshness",
    }
    hard = out.components["hard_criteria"]
    assert (hard["hits"], hard["max"], hard["value"]) == (0, 0, float(_NEUTRAL))
    assert "Matched 0/0 hard criteria" in (out.rationale or "")


# --- range invariant ---------------------------------------------------------


@given(
    list_price=st.integers(min_value=0, max_value=5_000_000_00),
    avm=st.integers(min_value=1, max_value=5_000_000_00),
    dom=st.integers(min_value=0, max_value=3650),
    max_price=st.integers(min_value=1, max_value=5_000_000_00),
    min_beds=st.integers(min_value=0, max_value=12),
    matched=st.integers(min_value=0, max_value=8),
)
def test_score_and_confidence_stay_in_the_unit_interval(
    *,
    list_price: int,
    avm: int,
    dom: int,
    max_price: int,
    min_beds: int,
    matched: int,
) -> None:
    """Keyword-only on purpose: six positional parameters trips PLR0917.

    `@given` supplies them by keyword anyway, so the `*` costs nothing and
    keeps the strategy name visibly bound to the parameter it fills.
    """
    out = _score(
        profile=_profile(max_price_cents=max_price, min_bedrooms=min_beds, must_haves=["a", "b"]),
        must_have_matches=["x"] * matched,
        pctx=_pctx(list_price_cents=list_price, avm_value_cents=avm, days_on_market=dom),
    )
    assert Decimal("0") <= out.score <= Decimal("1")
    assert Decimal("0") <= out.confidence <= Decimal("1")
    for component in out.components.values():
        assert 0.0 <= float(component["value"]) <= 1.0
