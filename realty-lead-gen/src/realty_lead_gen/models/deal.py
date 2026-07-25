"""DealAnalysis — the fused, per-property "what's the deal here" record.

Materialized by the pipeline after enrichment completes. This is the
row a realtor / investor looks at first, and the row Score depends on.
Kept append-only (versioned by ``analysis_version``) so downstream
audit is trivial.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from realty_lead_gen.models.base import Base, TimestampMixin, UUIDPKMixin
from realty_lead_gen.utils.jsontypes import JSONDict

if TYPE_CHECKING:
    from realty_lead_gen.models.property import Property


class DealAnalysis(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "deal_analysis"

    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("property.id", ondelete="CASCADE"),
        nullable=False,
    )
    analysis_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)

    # --- Overall condition (aggregated across photos) ------------------------
    overall_condition: Mapped[str | None] = mapped_column(String(16))  # UADCondition value
    condition_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))

    # --- Rehab estimate ------------------------------------------------------
    rehab_low_cents: Mapped[int | None] = mapped_column(Integer)
    rehab_high_cents: Mapped[int | None] = mapped_column(Integer)
    # Itemized list of {system, item, scope, cost_low, cost_high, confidence, evidence_photo_ids}
    rehab_line_items: Mapped[list[JSONDict]] = mapped_column(JSONB, nullable=False, default=list)

    # --- Valuation -----------------------------------------------------------
    avm_value_cents: Mapped[int | None] = mapped_column(Integer)
    avm_low_cents: Mapped[int | None] = mapped_column(Integer)
    avm_high_cents: Mapped[int | None] = mapped_column(Integer)
    avm_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    avm_provider: Mapped[str | None] = mapped_column(String(64))

    # ARV derived from renovated comps (see scoring/flipper.py)
    arv_cents: Mapped[int | None] = mapped_column(Integer)
    arv_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))

    # Estimated rent for buy-and-hold analysis
    monthly_rent_cents: Mapped[int | None] = mapped_column(Integer)

    # --- Comparable sales selected + LLM re-rank notes -----------------------
    comps: Mapped[list[JSONDict]] = mapped_column(JSONB, nullable=False, default=list)
    comps_narrative: Mapped[str | None] = mapped_column(Text)

    # --- Red flags aggregated ------------------------------------------------
    red_flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    # --- Human-readable narrative (for the "explain the lead" surface) --------
    narrative: Mapped[str | None] = mapped_column(Text)
    quality_gate_flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    property: Mapped[Property] = relationship(back_populates="deals", lazy="joined")

    __table_args__ = (
        Index("ix_deal_analysis__property_version", "property_id", "analysis_version"),
    )
