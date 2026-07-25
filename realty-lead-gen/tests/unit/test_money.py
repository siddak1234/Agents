"""Money conversion — deterministic, banker's rounding."""

from __future__ import annotations

from decimal import Decimal

import pytest

from realty_lead_gen.utils.money import cents_to_dollars, dollars_to_cents, usd


@pytest.mark.unit
class TestMoney:
    def test_dollars_to_cents_int(self) -> None:
        assert dollars_to_cents(100) == 10_000

    def test_dollars_to_cents_bankers_rounding(self) -> None:
        # 0.005 rounds to even -> 0.00 -> 0 cents
        assert dollars_to_cents(Decimal("0.005")) == 0
        # 0.015 rounds to even -> 0.02 -> 2 cents
        assert dollars_to_cents(Decimal("0.015")) == 2

    def test_roundtrip(self) -> None:
        assert cents_to_dollars(dollars_to_cents(Decimal("1234.56"))) == Decimal("1234.56")

    def test_usd_formatting(self) -> None:
        assert usd(1_234_567) == "$12,345.67"
        assert usd(None) == "n/a"
