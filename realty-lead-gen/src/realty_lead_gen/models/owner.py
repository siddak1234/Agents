"""Owners and the ownership timeline for each property.

Owners are separate from Contact channels — an owner might have many
phone numbers, all with independent right-party-contact probabilities.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Date, Enum, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from realty_lead_gen.models.base import Base, TimestampMixin, UUIDPKMixin
from realty_lead_gen.utils.jsontypes import JSONDict

if TYPE_CHECKING:
    from realty_lead_gen.models.contact import ContactChannel
    from realty_lead_gen.models.property import Property


class OwnerType(StrEnum):
    individual = "individual"
    trust = "trust"
    llc = "llc"
    corporation = "corporation"
    government = "government"
    unknown = "unknown"


class Owner(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner"

    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(96))
    middle_name: Mapped[str | None] = mapped_column(String(96))
    last_name: Mapped[str | None] = mapped_column(String(96))
    owner_type: Mapped[OwnerType] = mapped_column(
        Enum(OwnerType, name="owner_type", create_type=True),
        nullable=False,
        default=OwnerType.individual,
    )

    # For LLC / trust: mailing address (typically different from the property).
    mailing_address: Mapped[str | None] = mapped_column(String(512))

    # Aggregated data we've enriched over time — kept as JSONB because the
    # shape varies by skip-tracing provider.
    enriched_attributes: Mapped[JSONDict] = mapped_column(JSONB, nullable=False, default=dict)

    ownerships: Mapped[list[PropertyOwnership]] = relationship(back_populates="owner", lazy="raise")
    contacts: Mapped[list[ContactChannel]] = relationship(back_populates="owner", lazy="raise")

    __table_args__ = (Index("ix_owner__last_name", "last_name"),)


class PropertyOwnership(Base, UUIDPKMixin, TimestampMixin):
    """Ownership interval — supports historical + fractional ownership."""

    __tablename__ = "property_ownership"

    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("property.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("owner.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ownership_percent: Mapped[Decimal] = mapped_column(
        Numeric(6, 3), nullable=False, default=Decimal("100.000")
    )
    acquired_date: Mapped[date | None] = mapped_column(Date)
    sold_date: Mapped[date | None] = mapped_column(Date)
    acquisition_price_cents: Mapped[int | None] = mapped_column()  # nullable; not always known

    property: Mapped[Property] = relationship(back_populates="ownerships", lazy="joined")
    owner: Mapped[Owner] = relationship(back_populates="ownerships", lazy="joined")

    __table_args__ = (
        Index("ix_property_ownership__property_acquired", "property_id", "acquired_date"),
        Index("ix_property_ownership__owner", "owner_id"),
    )
