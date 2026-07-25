"""Per-persona property scores.

Every property that survives enrichment gets scored once per active
persona (flipper, wholesaler, buyer's agent). Scores are stored so
downstream ``Lead`` materialization is cheap and the "why did it
appear?" audit is direct.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from realty_lead_gen.models.base import Base, TimestampMixin, UUIDPKMixin
from realty_lead_gen.utils.jsontypes import JSONDict


class Persona(StrEnum):
    flipper = "flipper"
    wholesaler = "wholesaler"
    buyers_agent = "buyers_agent"
    listing_agent = "listing_agent"


class Score(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "score"

    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("property.id", ondelete="CASCADE"),
        nullable=False,
    )
    deal_analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("deal_analysis.id", ondelete="SET NULL")
    )
    persona: Mapped[Persona] = mapped_column(
        Enum(Persona, name="persona", create_type=True),
        nullable=False,
    )
    scorer_version: Mapped[str] = mapped_column(String(32), nullable=False)

    # 0..1 normalized composite score. Higher = better fit.
    score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    # Confidence in the score itself (independent of the score value).
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)

    # Component breakdown — {component_name: {value, weight, note}}. Enables
    # explainability in the API without re-running the scorer.
    components: Mapped[JSONDict] = mapped_column(JSONB, nullable=False, default=dict)
    rationale: Mapped[str | None] = mapped_column(String(2048))

    __table_args__ = (
        Index(
            "uq_score__property_persona_version",
            "property_id",
            "persona",
            "scorer_version",
            unique=True,
        ),
        Index("ix_score__persona_score", "persona", "score"),
    )
