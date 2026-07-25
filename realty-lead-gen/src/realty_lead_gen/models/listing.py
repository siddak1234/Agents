"""Listings — MLS or portal-originated for-sale/for-rent records.

A listing is *not* the property. One property can accumulate many
listings over years. We keep listings distinct from snapshots so agent
remarks, seller concessions, and marketing status can be reasoned about
independently.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from realty_lead_gen.models.base import Base, TimestampMixin, UUIDPKMixin
from realty_lead_gen.models.property import SnapshotSource
from realty_lead_gen.utils.jsontypes import JSONDict

if TYPE_CHECKING:
    from realty_lead_gen.models.property import Property


class ListingStatus(StrEnum):
    active = "active"
    pending = "pending"
    contingent = "contingent"
    coming_soon = "coming_soon"
    sold = "sold"
    withdrawn = "withdrawn"
    expired = "expired"
    canceled = "canceled"
    unknown = "unknown"


class ListingIntent(StrEnum):
    for_sale = "for_sale"
    for_rent = "for_rent"
    auction = "auction"
    fsbo = "fsbo"


class Listing(Base, UUIDPKMixin, TimestampMixin):
    """A single MLS/portal listing record for a property."""

    __tablename__ = "listing"

    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("property.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[SnapshotSource] = mapped_column(
        Enum(SnapshotSource, name="snapshot_source", create_type=False),
        nullable=False,
    )
    source_listing_id: Mapped[str] = mapped_column(String(128), nullable=False)
    mls_number: Mapped[str | None] = mapped_column(String(64))

    intent: Mapped[ListingIntent] = mapped_column(
        Enum(ListingIntent, name="listing_intent", create_type=True),
        nullable=False,
    )
    status: Mapped[ListingStatus] = mapped_column(
        Enum(ListingStatus, name="listing_status", create_type=True),
        nullable=False,
        default=ListingStatus.unknown,
    )

    list_price_cents: Mapped[int | None] = mapped_column(Integer)
    original_list_price_cents: Mapped[int | None] = mapped_column(Integer)
    list_date: Mapped[date | None] = mapped_column(Date)
    close_date: Mapped[date | None] = mapped_column(Date)
    close_price_cents: Mapped[int | None] = mapped_column(Integer)

    listing_agent_name: Mapped[str | None] = mapped_column(String(255))
    listing_agent_phone: Mapped[str | None] = mapped_column(String(32))
    listing_agent_email: Mapped[str | None] = mapped_column(String(255))
    listing_office_name: Mapped[str | None] = mapped_column(String(255))

    remarks_public: Mapped[str | None] = mapped_column(Text)
    remarks_private: Mapped[str | None] = mapped_column(Text)  # broker-only, if licensed

    photos_url_manifest: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    raw_payload: Mapped[JSONDict] = mapped_column(JSONB, nullable=False, default=dict)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    property: Mapped[Property] = relationship(back_populates="listings", lazy="joined")

    __table_args__ = (
        Index(
            "uq_listing__source_source_listing_id",
            "source",
            "source_listing_id",
            unique=True,
        ),
        Index("ix_listing__property_status", "property_id", "status"),
        Index("ix_listing__list_date", "list_date"),
    )
