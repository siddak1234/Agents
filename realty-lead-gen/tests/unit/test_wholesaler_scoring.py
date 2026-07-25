"""Wholesaler scoring."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from realty_lead_gen.models.property import Property, PropertyType
from realty_lead_gen.models.signal import Signal, SignalKind
from realty_lead_gen.scoring.base import PropertyContext
from realty_lead_gen.scoring.wholesaler import WholesalerScorer


def _prop() -> Property:
    return Property(
        id=uuid.uuid4(),
        address_hash="b" * 64,
        street_name="OAK RD",
        city="ANYTOWN",
        state="TX",
        postal_code="78701",
        property_type=PropertyType.single_family,
        attributes={},
    )


def _signal(kind: SignalKind, strength: float = 0.8) -> Signal:
    return Signal(
        id=uuid.uuid4(),
        property_id=uuid.uuid4(),
        kind=kind,
        observed_on=date(2026, 6, 1),
        strength=Decimal(str(strength)),
        source="test",
        source_reference=None,
        payload={},
    )


def _ctx(signals: list[Signal] | None = None, **kw) -> PropertyContext:
    d: dict = {
        "property": _prop(),
        "signals": signals or [],
        "avm_value_cents": 300_000_00,
        "avm_low_cents": 285_000_00,
        "avm_high_cents": 315_000_00,
        "avm_confidence": Decimal("0.7"),
        "arv_cents": 320_000_00,
        "monthly_rent_cents": None,
        "rehab_low_cents": 20_000_00,
        "rehab_high_cents": 30_000_00,
        "overall_condition": "C4",
        "condition_confidence": Decimal("0.7"),
        "red_flags": [],
        "list_price_cents": 175_000_00,
        "days_on_market": 45,
    }
    d.update(kw)
    return PropertyContext(**d)


@pytest.mark.unit
class TestWholesalerScorer:
    def test_no_motivation_scores_below_motivated(self) -> None:
        cold = WholesalerScorer().score(_ctx())
        hot = WholesalerScorer().score(
            _ctx(signals=[_signal(SignalKind.nod_filed), _signal(SignalKind.high_equity)])
        )
        assert hot.score > cold.score

    def test_missing_data_returns_zero(self) -> None:
        out = WholesalerScorer().score(_ctx(arv_cents=None, avm_high_cents=None))
        assert out.score == Decimal("0.0000")

    def test_spread_component_positive_for_deep_discount(self) -> None:
        out = WholesalerScorer().score(_ctx(list_price_cents=140_000_00))
        assert out.components["assignability_spread"]["value"] > 0.5

    def test_nod_weighted_higher_than_absentee(self) -> None:
        with_nod = WholesalerScorer().score(_ctx(signals=[_signal(SignalKind.nod_filed)]))
        with_absentee = WholesalerScorer().score(_ctx(signals=[_signal(SignalKind.absentee_owner)]))
        assert with_nod.score > with_absentee.score
