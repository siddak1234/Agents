"""Buyer profiles + saved searches.

BuyerProfile captures what an agent's client (or a self-serve buyer)
is looking for. Powers buyer-side lead gen — matches a new listing
to interested buyers, not just interested-property to investors.

SavedSearch is the geographic + criteria filter a user configures.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from realty_lead_gen.models.base import Base, TimestampMixin, UUIDPKMixin
from realty_lead_gen.models.score import Persona
from realty_lead_gen.utils.jsontypes import JSONDict

if TYPE_CHECKING:
    from realty_lead_gen.models.user import User


class BuyerReadiness(StrEnum):
    pre_qualified = "pre_qualified"
    pre_approved = "pre_approved"
    cash = "cash"
    just_looking = "just_looking"
    unknown = "unknown"


class BuyerProfile(Base, UUIDPKMixin, TimestampMixin):
    """The client-behind-the-agent view (for buyer's agents)."""

    __tablename__ = "buyer_profile"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    phone_e164: Mapped[str | None] = mapped_column(String(32))

    # Search criteria
    target_cities: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    target_postal_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    max_price_cents: Mapped[int | None] = mapped_column(Integer)
    min_price_cents: Mapped[int | None] = mapped_column(Integer)
    min_bedrooms: Mapped[int | None] = mapped_column(Integer)
    min_bathrooms: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    min_living_area_sqft: Mapped[int | None] = mapped_column(Integer)
    max_living_area_sqft: Mapped[int | None] = mapped_column(Integer)
    property_types: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    must_haves: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    nice_to_haves: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    deal_breakers: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    readiness: Mapped[BuyerReadiness] = mapped_column(
        Enum(BuyerReadiness, name="buyer_readiness", create_type=True),
        nullable=False,
        default=BuyerReadiness.unknown,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user: Mapped[User] = relationship(lazy="joined")

    __table_args__ = (Index("ix_buyer_profile__user_active", "user_id", "is_active"),)


class SavedSearch(Base, UUIDPKMixin, TimestampMixin):
    """A user-configured recurring search (per-persona)."""

    __tablename__ = "saved_search"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    persona: Mapped[Persona] = mapped_column(
        Enum(Persona, name="persona", create_type=False),
        nullable=False,
    )

    # Geographic scope. Any of the three may be set; conjunctive within a type.
    postal_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    cities: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    regions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    # Score threshold for a property to be surfaced as a lead.
    min_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, default=Decimal("0.5")
    )

    # Free-form criteria overlay (interpreted by scoring/matching layer)
    criteria: Mapped[JSONDict] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user: Mapped[User] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_saved_search__user_persona_active", "user_id", "persona", "is_active"),
    )
