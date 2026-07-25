"""Photos and their LLM-derived condition analyses.

Two tables:
    * ``photo`` — the raw photo URL and a perceptual hash (for dedup across
      re-uses of the same image across MLS listings).
    * ``photo_analysis`` — the output of the vision LLM (UAD condition grade,
      itemized repairs, evidence quotes). One photo can accumulate multiple
      analyses across model upgrades; we keep them all and pick the newest
      of trusted quality at read time.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from realty_lead_gen.models.base import Base, TimestampMixin, UUIDPKMixin
from realty_lead_gen.utils.jsontypes import JSONDict

if TYPE_CHECKING:
    from realty_lead_gen.models.property import Property


class UADCondition(StrEnum):
    """Fannie Mae UAD condition ratings. See docs/ARCHITECTURE.md."""

    C1 = "C1"  # new construction, unoccupied
    C2 = "C2"  # no deferred maintenance, updated
    C3 = "C3"  # well maintained, normal wear
    C4 = "C4"  # minor deferred maintenance
    C5 = "C5"  # obvious deferred maintenance
    C6 = "C6"  # unsafe / uninhabitable
    NOT_VISIBLE = "NOT_VISIBLE"


class RoomType(StrEnum):
    exterior = "exterior"
    kitchen = "kitchen"
    bathroom = "bathroom"
    bedroom = "bedroom"
    living_room = "living_room"
    dining_room = "dining_room"
    basement = "basement"
    garage = "garage"
    utility = "utility"
    yard = "yard"
    aerial = "aerial"
    floor_plan = "floor_plan"
    other = "other"


class Photo(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "photo"

    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("property.id", ondelete="CASCADE"),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    # sha256 of `url`. The URL itself is too wide to index safely (Postgres
    # btree tuples cap at ~2704 bytes and a 2048-char UTF-8 URL can exceed
    # that), so the uniqueness guarantee rides on a fixed-width digest.
    # This is what makes re-ingest idempotent at the DB level rather than
    # only in application code.
    url_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # 16-hex-char perceptual hash (dhash / phash) for cross-listing dedup.
    perceptual_hash: Mapped[str | None] = mapped_column(String(32), index=True)
    width_px: Mapped[int | None] = mapped_column(Integer)
    height_px: Mapped[int | None] = mapped_column(Integer)
    byte_size: Mapped[int | None] = mapped_column(Integer)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    caption: Mapped[str | None] = mapped_column(String(1024))
    source_url: Mapped[str | None] = mapped_column(String(2048))

    property: Mapped[Property] = relationship(back_populates="photos", lazy="joined")
    analyses: Mapped[list[PhotoAnalysis]] = relationship(back_populates="photo", lazy="raise")

    __table_args__ = (
        UniqueConstraint(
            "property_id",
            "url_sha256",
            name="uq_photo__property_id__url_sha256",
        ),
        Index("ix_photo__property_order", "property_id", "order_index"),
    )


class PhotoAnalysis(Base, UUIDPKMixin, TimestampMixin):
    """One vision-LLM pass over one photo."""

    __tablename__ = "photo_analysis"

    photo_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("photo.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    room_type: Mapped[RoomType] = mapped_column(
        Enum(RoomType, name="room_type", create_type=True),
        nullable=False,
        default=RoomType.other,
    )
    condition: Mapped[UADCondition] = mapped_column(
        Enum(UADCondition, name="uad_condition", create_type=True),
        nullable=False,
        default=UADCondition.NOT_VISIBLE,
    )
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)  # 0..1

    # Structured findings: list[{item, scope, cost_low_usd, cost_high_usd, evidence, confidence}]
    findings: Mapped[list[JSONDict]] = mapped_column(JSONB, nullable=False, default=list)
    observations: Mapped[str | None] = mapped_column(String(2048))
    red_flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd_micros: Mapped[int | None] = mapped_column(Integer)  # 1M micros = $1

    photo: Mapped[Photo] = relationship(back_populates="analyses", lazy="joined")

    __table_args__ = (Index("ix_photo_analysis__photo_analyzed", "photo_id", "analyzed_at"),)
