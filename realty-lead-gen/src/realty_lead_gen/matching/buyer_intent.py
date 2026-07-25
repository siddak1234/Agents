"""Buyer <-> property matcher.

Given a property and a set of active buyer profiles, find which buyers
this property satisfies (or nearly satisfies). Uses hard-criteria
matching + a cheap fuzzy score for must-haves.

Structure note: each hard criterion is its own function returning a
``_Verdict``, and ``_CRITERIA`` is the ordered list ``match()`` walks. This
started as one inlined ``if`` ladder and was decomposed when it crossed a
complexity threshold — but the reason to keep it decomposed is not the
lint. A buyer criterion has three distinct outcomes that the ladder form
conflated into control flow:

* **not configured** — the buyer never stated a preference, so the
  criterion must not count toward ``hard_total`` (a buyer with no stated
  city is not "failing" the city test, and inflating the denominator would
  silently push every match ratio down),
* **configured and satisfied** — counts toward both, and may contribute a
  human-readable reason,
* **configured and violated** — a dealbreaker, which discards the profile
  outright.

Making that a returned value rather than a branch is what lets a new
criterion be added by writing one function and appending it to the list,
with no way to forget the ``total += 1``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from realty_lead_gen.models.buyer import BuyerProfile
    from realty_lead_gen.models.property import Property

    # A criterion sees the property, the list price (which lives on the
    # listing snapshot rather than the property, hence the separate
    # argument), and the profile. Returning `None` means "not configured
    # for this buyer". Type-checking-only because nothing evaluates it at
    # runtime: `_CRITERIA`'s annotation is a string under
    # `from __future__ import annotations`.
    type _Criterion = Callable[[Property, int | None, BuyerProfile], _Verdict | None]


@dataclass(frozen=True, slots=True)
class BuyerMatch:
    profile_id: str  # BuyerProfile.id UUID string
    hard_hits: int
    hard_total: int
    reasons: list[str] = field(default_factory=list)
    dealbreakers: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Verdict:
    """One criterion's outcome for one (property, buyer) pair.

    ``None`` from a criterion function means "the buyer did not configure
    this", which is different from a satisfied criterion: it is not counted
    at all. Only ``_Verdict`` instances reach the tally.
    """

    satisfied: bool
    #: Shown to the buyer's agent on a match. Deliberately optional: some
    #: criteria (price ceiling, minimum square footage) are only interesting
    #: when they *fail*, and listing "under budget" as a selling point is
    #: noise.
    reason: str | None = None
    #: Why this profile is disqualified. Only read when `satisfied` is False.
    dealbreaker: str | None = None


def _postal_code(prop: Property, _price: int | None, profile: BuyerProfile) -> _Verdict | None:
    if not profile.target_postal_codes:
        return None
    if prop.postal_code in profile.target_postal_codes:
        return _Verdict(satisfied=True, reason=f"in target zip {prop.postal_code}")
    return _Verdict(
        satisfied=False,
        dealbreaker=f"zip {prop.postal_code} not in buyer targets",
    )


def _city(prop: Property, _price: int | None, profile: BuyerProfile) -> _Verdict | None:
    if not profile.target_cities:
        return None
    if prop.city in profile.target_cities:
        return _Verdict(satisfied=True, reason=f"in target city {prop.city}")
    return _Verdict(satisfied=False, dealbreaker=f"city {prop.city} not in buyer targets")


def _price_ceiling(_prop: Property, price: int | None, profile: BuyerProfile) -> _Verdict | None:
    # An unknown list price is not a violation — an off-market or
    # pre-foreclosure record legitimately has none, and disqualifying those
    # would silently drop exactly the inventory the wholesaler persona
    # exists to find.
    if profile.max_price_cents is None or price is None:
        return None
    if price <= profile.max_price_cents:
        return _Verdict(satisfied=True)
    return _Verdict(satisfied=False, dealbreaker=f"list price ${price / 100:,.0f} exceeds max")


def _bedrooms(prop: Property, _price: int | None, profile: BuyerProfile) -> _Verdict | None:
    if profile.min_bedrooms is None:
        return None
    if prop.bedrooms is not None and prop.bedrooms >= profile.min_bedrooms:
        return _Verdict(satisfied=True, reason=f"{prop.bedrooms}br >= {profile.min_bedrooms}")
    # Unknown bedroom count fails a stated minimum on purpose: the buyer
    # asked for a floor, and "we don't know" is not evidence the floor is met.
    return _Verdict(satisfied=False, dealbreaker="bedrooms below min")


def _living_area(prop: Property, _price: int | None, profile: BuyerProfile) -> _Verdict | None:
    if profile.min_living_area_sqft is None:
        return None
    if prop.living_area_sqft is not None and prop.living_area_sqft >= profile.min_living_area_sqft:
        return _Verdict(satisfied=True)
    return _Verdict(satisfied=False, dealbreaker="living area below min")


def _property_type(prop: Property, _price: int | None, profile: BuyerProfile) -> _Verdict | None:
    if not profile.property_types:
        return None
    kind = prop.property_type.value
    if kind in profile.property_types:
        return _Verdict(satisfied=True, reason=f"type {kind} matches")
    return _Verdict(satisfied=False, dealbreaker=f"type {kind} not in target")


#: Evaluation order, which is also the order reasons appear to the user.
_CRITERIA: Final[Sequence[_Criterion]] = (
    _postal_code,
    _city,
    _price_ceiling,
    _bedrooms,
    _living_area,
    _property_type,
)


class BuyerMatcher:
    """Deterministic filter — LLM-optional soft matching lives elsewhere."""

    def match(
        self,
        property_: Property,
        list_price_cents: int | None,
        buyer_profiles: list[BuyerProfile],
    ) -> list[BuyerMatch]:
        matches: list[BuyerMatch] = []
        for profile in buyer_profiles:
            if not profile.is_active:
                continue
            match = self._match_one(property_, list_price_cents, profile)
            if match is not None:
                matches.append(match)
        return matches

    @staticmethod
    def _match_one(
        property_: Property,
        list_price_cents: int | None,
        profile: BuyerProfile,
    ) -> BuyerMatch | None:
        """One profile's verdict, or `None` if any hard criterion is violated.

        Every criterion is evaluated even after the first violation. That is
        deliberate: it costs nothing here (these are field comparisons, not
        queries) and it means the day this returns near-misses instead of
        discarding them, the caller already has the complete list of what
        was wrong rather than only the first thing checked.
        """
        hits = 0
        total = 0
        reasons: list[str] = []
        violated = False

        for criterion in _CRITERIA:
            verdict = criterion(property_, list_price_cents, profile)
            if verdict is None:
                continue
            total += 1
            if verdict.satisfied:
                hits += 1
                if verdict.reason:
                    reasons.append(verdict.reason)
            else:
                violated = True

        if violated:
            return None

        return BuyerMatch(
            profile_id=str(profile.id),
            hard_hits=hits,
            hard_total=total,
            reasons=reasons,
            dealbreakers=[],
        )
