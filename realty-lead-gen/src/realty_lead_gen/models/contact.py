"""Contact channels enriched via skip trace.

TCPA-sensitive. See ARCHITECTURE.md for the compliance flow — never
place a channel into an outbound-callable state without an accompanying
Consent record (out of scope for MVP but the schema is designed to add
it without disruption).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from realty_lead_gen.models.base import Base, TimestampMixin, UUIDPKMixin
from realty_lead_gen.utils.jsontypes import JSONDict

if TYPE_CHECKING:
    from realty_lead_gen.models.owner import Owner


class ChannelKind(StrEnum):
    phone_mobile = "phone_mobile"
    phone_landline = "phone_landline"
    phone_voip = "phone_voip"
    email = "email"
    mailing_address = "mailing_address"


class ContactChannel(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "contact_channel"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("owner.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[ChannelKind] = mapped_column(
        Enum(ChannelKind, name="channel_kind", create_type=True),
        nullable=False,
    )
    # Normalized: E.164 for phones, lowercased for email
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    value_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 for dedup

    # Right-party-contact probability from the skip-trace provider (0..1).
    rpc_probability: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))

    # DNC scrub + TCPA flags. `is_dnc` sourced from ReassignedNumbersDatabase or provider.
    is_dnc: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    provider: Mapped[str | None] = mapped_column(String(64))  # e.g. batchskiptracing
    provider_payload: Mapped[JSONDict] = mapped_column(JSONB, nullable=False, default=dict)

    owner: Mapped[Owner] = relationship(back_populates="contacts", lazy="joined")

    __table_args__ = (
        Index(
            "uq_contact_channel__owner_kind_value_hash",
            "owner_id",
            "kind",
            "value_hash",
            unique=True,
        ),
        Index("ix_contact_channel__value_hash", "value_hash"),
    )
