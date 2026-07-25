from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from realty_lead_gen.matching.buyer_intent import BuyerMatcher
from realty_lead_gen.models.buyer import BuyerProfile, BuyerReadiness
from realty_lead_gen.models.property import Property, PropertyType


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
    d: dict = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "display_name": "Test",
        "target_cities": ["AUSTIN"],
        "target_postal_codes": ["78701"],
        "max_price_cents": 500_000_00,
        "min_price_cents": None,
        "min_bedrooms": 2,
        "min_bathrooms": None,
        "min_living_area_sqft": 1200,
        "max_living_area_sqft": None,
        "property_types": ["single_family"],
        "must_haves": [],
        "nice_to_haves": [],
        "deal_breakers": [],
        "readiness": BuyerReadiness.pre_approved,
        "is_active": True,
    }
    d.update(kw)
    return BuyerProfile(**d)


@pytest.mark.unit
class TestBuyerMatcher:
    def test_all_criteria_match(self) -> None:
        matches = BuyerMatcher().match(_prop(), 450_000_00, [_profile()])
        assert len(matches) == 1
        assert matches[0].hard_hits == matches[0].hard_total

    def test_over_budget_excluded(self) -> None:
        matches = BuyerMatcher().match(_prop(), 700_000_00, [_profile()])
        assert not matches

    def test_wrong_city_excluded(self) -> None:
        matches = BuyerMatcher().match(_prop(city="DALLAS"), 400_000_00, [_profile()])
        assert not matches

    def test_property_type_mismatch(self) -> None:
        matches = BuyerMatcher().match(
            _prop(property_type=PropertyType.condo), 400_000_00, [_profile()]
        )
        assert not matches
