"""Flipper scoring — deterministic, no I/O."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from realty_lead_gen.models.property import Property, PropertyType
from realty_lead_gen.scoring.base import PropertyContext
from realty_lead_gen.scoring.flipper import FlipperScorer


def _make_property() -> Property:
    p = Property(
        id=uuid.uuid4(),
        address_hash="a" * 64,
        street_name="MAIN ST",
        city="ANYTOWN",
        state="TX",
        postal_code="78701",
        property_type=PropertyType.single_family,
        attributes={},
    )
    return p


def _ctx(**overrides) -> PropertyContext:
    p = _make_property()
    defaults: dict = {
        "property": p,
        "signals": [],
        "avm_value_cents": 300_000_00,
        "avm_low_cents": 285_000_00,
        "avm_high_cents": 315_000_00,
        "avm_confidence": Decimal("0.8"),
        "arv_cents": 320_000_00,
        "monthly_rent_cents": 2_200_00,
        "rehab_low_cents": 25_000_00,
        "rehab_high_cents": 35_000_00,
        "overall_condition": "C4",
        "condition_confidence": Decimal("0.7"),
        "red_flags": [],
        "list_price_cents": 190_000_00,
        "days_on_market": 30,
    }
    defaults.update(overrides)
    return PropertyContext(**defaults)


@pytest.mark.unit
class TestFlipperScorer:
    def test_good_deal_scores_high(self) -> None:
        out = FlipperScorer().score(_ctx())
        assert out.score > Decimal("0.5")
        assert "MAO" in (out.rationale or "")

    def test_bad_deal_scores_low(self) -> None:
        # asking price above MAO (0.70 * 320000 - 35000 = 189000)
        out = FlipperScorer().score(_ctx(list_price_cents=280_000_00))
        assert out.score < Decimal("0.5")

    def test_missing_arv_returns_zero(self) -> None:
        out = FlipperScorer().score(_ctx(arv_cents=None, avm_high_cents=None))
        assert out.score == Decimal("0.0000")
        assert "Insufficient data" in (out.rationale or "")

    def test_red_flag_penalty(self) -> None:
        clean = FlipperScorer().score(_ctx())
        flagged = FlipperScorer().score(_ctx(red_flags=["foundation", "roof"]))
        assert flagged.score < clean.score

    def test_condition_c4_beats_c2(self) -> None:
        c4 = FlipperScorer().score(_ctx(overall_condition="C4"))
        c2 = FlipperScorer().score(_ctx(overall_condition="C2"))
        assert c4.score > c2.score

    @given(
        arv=st.integers(min_value=100_000_00, max_value=1_000_000_00),
        list_price=st.integers(min_value=50_000_00, max_value=1_500_000_00),
        rehab_high=st.integers(min_value=0, max_value=200_000_00),
    )
    def test_score_always_in_unit_interval(
        self, arv: int, list_price: int, rehab_high: int
    ) -> None:
        out = FlipperScorer().score(
            _ctx(
                arv_cents=arv,
                avm_value_cents=arv,
                avm_high_cents=arv,
                list_price_cents=list_price,
                rehab_low_cents=int(rehab_high * 0.7),
                rehab_high_cents=rehab_high,
            )
        )
        assert Decimal("0") <= out.score <= Decimal("1")
