"""Money helpers.

We store money as cents (integers). Never floats. Convert at API and
LLM prompt boundaries.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal


def dollars_to_cents(amount: Decimal | float | int | str) -> int:
    """Convert to integer cents with banker's rounding."""
    d = Decimal(str(amount))
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def cents_to_dollars(cents: int) -> Decimal:
    """Convert integer cents back to a Decimal dollar amount."""
    return (Decimal(cents) / Decimal(100)).quantize(Decimal("0.01"))


def usd(cents: int | None) -> str:
    """Format for logs / rationales. Returns 'n/a' for None."""
    if cents is None:
        return "n/a"
    dollars = Decimal(cents) / Decimal(100)
    return f"${dollars:,.2f}"
