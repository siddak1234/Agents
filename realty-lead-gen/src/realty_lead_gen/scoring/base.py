"""Scoring protocol + context container.

Every scorer takes a fully enriched :class:`PropertyContext` and returns
a :class:`ScoreOutput`. Scores are deterministic (no LLM call inside
the scorer itself — LLM narrative belongs to
:mod:`realty_lead_gen.scoring.explanations`), so unit tests are
straightforward.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from realty_lead_gen.models.property import Property
from realty_lead_gen.models.signal import Signal


@dataclass(frozen=True, slots=True)
class PropertyContext:
    """Everything a scorer needs, pre-fetched."""

    property: Property
    signals: list[Signal]
    # From DealAnalysis
    avm_value_cents: int | None
    avm_low_cents: int | None
    avm_high_cents: int | None
    avm_confidence: Decimal | None
    arv_cents: int | None
    monthly_rent_cents: int | None
    rehab_low_cents: int | None
    rehab_high_cents: int | None
    overall_condition: str | None
    condition_confidence: Decimal | None
    red_flags: list[str]
    # From latest snapshot
    list_price_cents: int | None
    days_on_market: int | None
    # Region-specific caveats
    market_holding_months: float = 4.0  # avg months to flip in target market
    market_annual_appreciation: float = 0.03


@dataclass(frozen=True, slots=True)
class ScoreOutput:
    score: Decimal  # 0..1
    confidence: Decimal  # 0..1
    components: dict[str, dict[str, Any]] = field(default_factory=dict)
    rationale: str | None = None


class Scorer(Protocol):
    scorer_version: str

    def score(self, ctx: PropertyContext) -> ScoreOutput: ...


def clamp_unit(value: float | Decimal) -> Decimal:
    """Clamp a number to [0, 1] and return a 4-decimal Decimal."""
    v = Decimal(str(value)) if not isinstance(value, Decimal) else value
    if v < Decimal("0"):
        return Decimal("0.0000")
    if v > Decimal("1"):
        return Decimal("1.0000")
    return v.quantize(Decimal("0.0001"))
