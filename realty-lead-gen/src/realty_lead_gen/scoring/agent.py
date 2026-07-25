"""Buyer's-agent scoring — property-to-buyer-profile fit.

Given a property and (optionally) a specific BuyerProfile, score how
well the property matches the buyer's criteria. When no profile is
provided we score against a generic "general market interest" rubric.

Score components:

    * hard_criteria_match — bedrooms, baths, price, sqft, type, geo.
                            Deal-breakers zero this out.
    * must_haves_match    — LLM-optional soft match on free-form must-haves.
    * price_position      — under-market position from AVM
    * dom_bonus           — fresh listings preferred for competitiveness

Each component is computed by its own function returning a plain value in
[0, 1], and ``score_with_profile`` does nothing but assemble them. The
split is not only for readability: the component dictionaries are
persisted verbatim into ``Score.components`` (JSONB) and served through
``ScoreDTO``, so their shape is a wire contract. Keeping each one built in
exactly one place is what makes that contract auditable.

Relationship to :mod:`realty_lead_gen.matching.buyer_intent`: both compare
a property against a ``BuyerProfile``, and they deliberately answer
different questions. The matcher is a *filter* — a violated criterion
discards the buyer outright. This is a *ranker* — a missed criterion only
lowers a ratio.

How each treats *unknown* property data is a separate question, and here
this module is inconsistent with itself. Three fields on ``Property`` are
nullable — ``bedrooms``, ``bathrooms``, ``living_area_sqft`` — and a
missing square footage drops the criterion from both numerator and
denominator, while a missing bedroom or bathroom count is scored as an
outright miss with the denominator still incremented. A listing whose
bed/bath fields the upstream source never populated is therefore ranked as
though it had failed the buyer's requirements: the ranker punishing a gap
in *our* data, which is the one thing the drop-from-both rule exists to
avoid. That predates the decomposition of this module (the refactor was
verified output-identical against the previous implementation), so it is
recorded here and pinned by
``tests/unit/test_agent_scoring.py::test_unknown_bed_bath_is_punished_but_unknown_sqft_is_not``
rather than quietly changed. Unifying it — with each other, or with the
matcher's stricter filter rule — is a product decision that should arrive
with that test failing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Final

# Eager, not `TYPE_CHECKING`: both are annotations on `@dataclass` bodies,
# and this project treats those as runtime-evaluated (see the
# `runtime-evaluated-decorators` block in `pyproject.toml` — the scoring
# layer's dataclasses are read back by Pydantic's `TypeAdapter`).
from realty_lead_gen.models.buyer import BuyerProfile
from realty_lead_gen.scoring.base import PropertyContext, ScoreOutput, clamp_unit

if TYPE_CHECKING:
    # Only ever a function-parameter annotation, so it stays deferred.
    from realty_lead_gen.models.property import Property

SCORER_VERSION: Final[str] = "buyers_agent_v1"

# Component weights. Hard criteria dominate because they are the buyer's own
# stated, checkable requirements; everything else is inference on top.
# `test_agent_scoring.py` asserts these sum to exactly 1 — without that, the
# composite silently stops being a [0, 1] score and every downstream
# threshold (`SavedSearch.min_score`, the API's `min_score` filter) shifts.
_HARD_WEIGHT: Final[Decimal] = Decimal("0.55")
_MUST_HAVE_WEIGHT: Final[Decimal] = Decimal("0.20")
_PRICE_POSITION_WEIGHT: Final[Decimal] = Decimal("0.15")
_DOM_WEIGHT: Final[Decimal] = Decimal("0.10")

#: Returned by a component that has no evidence either way. 0.5 rather than
#: 0.0 so an unknown reads as "no signal", not "bad" — scoring a listing
#: down for missing AVM coverage would systematically bury rural and
#: new-construction inventory.
_NEUTRAL: Final[Decimal] = Decimal("0.5")

#: Days on market at which the freshness component reaches 0. Two months is
#: roughly when a buyer's agent stops treating a listing as competitive.
_DOM_ZERO_AT: Final[Decimal] = Decimal("60")

#: Confidence with and without any checkable hard criteria. Both are low by
#: design: this scorer has never been calibrated against closed-deal
#: outcomes, and a confident-looking number would invite trusting it as one.
_CONFIDENCE_WITH_CRITERIA: Final[Decimal] = Decimal("0.6")
_CONFIDENCE_WITHOUT_CRITERIA: Final[Decimal] = Decimal("0.3")


@dataclass(frozen=True, slots=True)
class BuyersAgentContext:
    property: PropertyContext
    profile: BuyerProfile | None = None
    must_have_matches: list[str] = field(default_factory=list)
    nice_to_have_matches: list[str] = field(default_factory=list)
    deal_breaker_hits: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _HardCriteria:
    """How many of the buyer's stated, checkable requirements this property meets.

    `maximum` is the count of criteria that were *evaluated*, which is not
    the count the buyer configured — but the rule for what gets evaluated
    is not uniform, so it has to be read in two parts:

    * a price bound is skipped when the listing carries no price, and a
      living-area bound is skipped when the property has no recorded square
      footage — dropped from numerator *and* denominator, so a data gap
      neither helps nor hurts;
    * a bedroom or bathroom minimum is **not** skipped when the property's
      count is unknown. It is counted as a miss against a denominator that
      still includes it.

    The second bullet is inherited behaviour rather than a considered
    choice; see the module docstring, and `test_agent_scoring.py` for the
    test that pins it.
    """

    hits: int
    maximum: int
    #: Per-criterion detail surfaced to the UI. Only the price bounds are
    #: recorded today — they are the two an agent most often wants to see
    #: spelled out ("why is this in my list at $612k?").
    notes: dict[str, object]

    @property
    def value(self) -> Decimal:
        if not self.maximum:
            return _NEUTRAL
        return clamp_unit(Decimal(self.hits) / Decimal(self.maximum))


def _hard_criteria(profile: BuyerProfile | None, ctx: PropertyContext) -> _HardCriteria:
    if profile is None:
        return _HardCriteria(hits=0, maximum=0, notes={})

    prop: Property = ctx.property
    price = ctx.list_price_cents
    checks: list[bool] = []
    notes: dict[str, object] = {}

    if profile.max_price_cents is not None and price is not None:
        ok = price <= profile.max_price_cents
        checks.append(ok)
        notes["max_price"] = ok
    if profile.min_price_cents is not None and price is not None:
        ok = price >= profile.min_price_cents
        checks.append(ok)
        notes["min_price"] = ok
    if profile.min_bedrooms is not None:
        checks.append(prop.bedrooms is not None and prop.bedrooms >= profile.min_bedrooms)
    if profile.min_bathrooms is not None:
        checks.append(prop.bathrooms is not None and prop.bathrooms >= profile.min_bathrooms)
    if profile.min_living_area_sqft is not None and prop.living_area_sqft is not None:
        checks.append(prop.living_area_sqft >= profile.min_living_area_sqft)
    if profile.max_living_area_sqft is not None and prop.living_area_sqft is not None:
        checks.append(prop.living_area_sqft <= profile.max_living_area_sqft)
    if profile.property_types:
        checks.append(prop.property_type.value in profile.property_types)
    if profile.target_postal_codes:
        checks.append(prop.postal_code in profile.target_postal_codes)
    if profile.target_cities:
        checks.append(prop.city in profile.target_cities)

    return _HardCriteria(hits=sum(checks), maximum=len(checks), notes=notes)


def _must_have_value(matches: list[str], profile: BuyerProfile | None) -> Decimal:
    """Fraction of the buyer's free-form must-haves the caller matched.

    The matching itself happens upstream (rules or an LLM pass); this only
    turns the caller's answer into a ratio. A buyer who stated no must-haves
    gets the neutral value — an empty requirement list is not 100% met, it
    is unexamined.
    """
    if not (profile and profile.must_haves):
        return _NEUTRAL
    return clamp_unit(Decimal(len(matches)) / Decimal(len(profile.must_haves)))


def _price_position_value(ctx: PropertyContext) -> Decimal:
    """Where the asking price sits against the automated valuation.

    `2 - 2 * (list / avm)` is a line through (1.0, 0.0) with slope -2, then
    clamped: at the AVM the component is 0, at 10% under it is 0.2, at 25%
    under it is 0.5, and it only saturates at 1.0 once the asking price is
    *half* the AVM. Anything at or above the AVM is 0.

    Two consequences worth stating, because neither is obvious from the
    expression. First, the curve is aggressive at the top: a listing 10%
    under the model — a good find in most markets — earns only a fifth of
    this component, so `price_position` contributes meaningfully to the
    composite only for genuinely distressed pricing. Second, saturation at
    half the AVM is far enough out that the clamp almost never binds on
    real MLS inventory; it matters mainly for auction and tax-sale records.
    Whether that is the right aggressiveness is a calibration question this
    scorer has never had outcome data to answer — see the confidence
    constants below, which is why they are deliberately low.
    (`tests/unit/test_agent_scoring.py::test_price_position_curve` pins
    these points; an earlier version of this docstring claimed saturation
    at 25% under, which the test disproved.)
    """
    if not (ctx.list_price_cents and ctx.avm_value_cents):
        return _NEUTRAL
    ratio = Decimal(ctx.list_price_cents) / Decimal(ctx.avm_value_cents)
    return clamp_unit(Decimal("2") - Decimal("2") * ratio)


def _dom_freshness_value(days_on_market: int) -> Decimal:
    """Linear decay from 1.0 on day 0 to 0.0 at `_DOM_ZERO_AT`.

    Fresh listings score higher because a buyer's agent competing for one
    needs to be early; this is the opposite of the aged-listing signal the
    flipper and wholesaler scorers reward, and that asymmetry is correct —
    the same listing is a worse opportunity for one persona precisely
    because it is a better one for another.
    """
    return clamp_unit(Decimal("1") - Decimal(days_on_market) / _DOM_ZERO_AT)


class BuyersAgentScorer:
    scorer_version: str = SCORER_VERSION

    def score_with_profile(self, ctx: BuyersAgentContext) -> ScoreOutput:
        if ctx.deal_breaker_hits:
            # A deal-breaker is not a low score, it is a disqualification.
            # Returned with high confidence because it required no
            # inference: the buyer named the condition and the property has
            # it.
            return ScoreOutput(
                score=Decimal("0.0000"),
                confidence=Decimal("0.900"),
                components={"deal_breakers": {"hits": ctx.deal_breaker_hits}},
                rationale=(f"Deal-breakers hit: {', '.join(ctx.deal_breaker_hits)}. Excluded."),
            )

        pc = ctx.property
        hard = _hard_criteria(ctx.profile, pc)
        must_have = _must_have_value(ctx.must_have_matches, ctx.profile)
        price_position = _price_position_value(pc)
        # An unknown DOM becomes 0, i.e. "listed today" — which is the one
        # place this scorer answers "no evidence" with full credit instead
        # of `_NEUTRAL`. A source that never populates `days_on_market`
        # therefore gets a systematic lift of up to `_DOM_WEIGHT` over one
        # that does. Left as-is because it is a tenth of the composite and
        # changing it is a calibration decision, but pinned by
        # `test_unknown_dom_is_treated_as_brand_new` so it stays visible.
        dom = pc.days_on_market or 0
        dom_freshness = _dom_freshness_value(dom)

        components: dict[str, dict[str, object]] = {
            "hard_criteria": {
                "value": float(hard.value),
                "weight": float(_HARD_WEIGHT),
                "hits": hard.hits,
                "max": hard.maximum,
                "notes": hard.notes,
            },
            "must_haves": {
                "value": float(must_have),
                "weight": float(_MUST_HAVE_WEIGHT),
                "matched": ctx.must_have_matches,
            },
            "price_position": {
                "value": float(price_position),
                "weight": float(_PRICE_POSITION_WEIGHT),
            },
            "dom_freshness": {
                "value": float(dom_freshness),
                "weight": float(_DOM_WEIGHT),
                "dom": dom,
            },
        }

        # Summed from the emitted dictionaries rather than the Decimals
        # above, so the persisted components always explain the score they
        # accompany — a weight edited in one place and not the other shows
        # up as a wrong total instead of an invisible disagreement.
        composite = clamp_unit(
            sum(
                (Decimal(str(c["value"])) * Decimal(str(c["weight"])) for c in components.values()),
                Decimal("0"),
            )
        )
        confidence = clamp_unit(
            _CONFIDENCE_WITH_CRITERIA if hard.maximum > 0 else _CONFIDENCE_WITHOUT_CRITERIA
        )

        rationale = (
            f"Matched {hard.hits}/{hard.maximum} hard criteria; "
            f"{len(ctx.must_have_matches)} must-haves matched; "
            f"price position {float(price_position):.2f}."
        )
        return ScoreOutput(
            score=composite,
            confidence=confidence,
            components=components,
            rationale=rationale,
        )
