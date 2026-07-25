"""Motivated-seller signals attached to a property.

Signals are additive evidence — each strengthens (or weakens) the case
that an owner is motivated to sell. Downstream scoring aggregates them
with per-signal weights; see scoring/wholesaler.py.
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
    from realty_lead_gen.models.property import Property


class SignalKind(StrEnum):
    """A signal that suggests an owner may be motivated to sell.

    Sourced from public records + third-party feeds. See ARCHITECTURE.md
    for the source-per-signal matrix and expected coverage.
    """

    nod_filed = "nod_filed"  # Notice of Default
    lis_pendens = "lis_pendens"
    tax_delinquent = "tax_delinquent"
    code_violation = "code_violation"
    vacancy_usps = "vacancy_usps"
    absentee_owner = "absentee_owner"
    high_equity = "high_equity"
    inherited_probate = "inherited_probate"
    divorce_filed = "divorce_filed"
    bankruptcy = "bankruptcy"
    long_term_ownership = "long_term_ownership"
    recent_price_cut = "recent_price_cut"
    aged_listing = "aged_listing"  # DOM > threshold
    withdrawn_recently = "withdrawn_recently"
    expired_listing = "expired_listing"
    other = "other"


class Signal(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "signal"

    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("property.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[SignalKind] = mapped_column(
        Enum(SignalKind, name="signal_kind", create_type=True),
        nullable=False,
    )
    observed_on: Mapped[date] = mapped_column(Date, nullable=False)
    # Signal strength as normalized [0..1] — how strong this evidence is
    # for the "owner is motivated to sell" hypothesis.
    strength: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(255))
    payload: Mapped[JSONDict] = mapped_column(JSONB, nullable=False, default=dict)

    property: Mapped[Property] = relationship(back_populates="signals", lazy="joined")

    __table_args__ = (
        Index("ix_signal__property_kind_observed", "property_id", "kind", "observed_on"),
        Index("ix_signal__kind_observed", "kind", "observed_on"),
    )
