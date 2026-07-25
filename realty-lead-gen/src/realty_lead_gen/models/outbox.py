"""Transactional outbox.

To reliably emit events (to the frontend, an external webhook, or an
internal event bus) without dual-writes, we insert an OutboxEvent in
the same DB transaction that mutates state, and a worker relays it
afterwards. Failed relays retry indefinitely with backoff.

Pattern reference: Chris Richardson, microservices.io/patterns/data/transactional-outbox.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from realty_lead_gen.models.base import Base, TimestampMixin, UUIDPKMixin
from realty_lead_gen.utils.jsontypes import JSONDict


class OutboxStatus(StrEnum):
    pending = "pending"
    dispatched = "dispatched"
    failed = "failed"


class OutboxEvent(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "outbox_event"

    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[JSONDict] = mapped_column(JSONB, nullable=False, default=dict)

    status: Mapped[OutboxStatus] = mapped_column(
        Enum(OutboxStatus, name="outbox_status", create_type=True),
        nullable=False,
        default=OutboxStatus.pending,
    )
    dispatch_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(2048))

    __table_args__ = (
        Index("ix_outbox_event__status_dispatch_after", "status", "dispatch_after"),
        Index("ix_outbox_event__aggregate", "aggregate_type", "aggregate_id"),
    )
