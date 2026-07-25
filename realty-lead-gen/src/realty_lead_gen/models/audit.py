"""Append-only audit trail.

Anything security-relevant or compliance-relevant lands here. Never
mutated, only inserted. Retention policy applied out-of-band (e.g. via
a partition drop or a periodic archive job) — not modeled here so we
don't accidentally imply mutability.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from realty_lead_gen.models.base import Base, UUIDPKMixin
from realty_lead_gen.utils.jsontypes import JSONDict


class AuditAction(StrEnum):
    user_login = "user_login"
    lead_viewed = "lead_viewed"
    lead_contacted = "lead_contacted"
    lead_feedback = "lead_feedback"
    search_saved = "search_saved"
    export = "export"
    admin_change = "admin_change"


class AuditEvent(Base, UUIDPKMixin):
    __tablename__ = "audit_event"

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_ip: Mapped[str | None] = mapped_column(String(45))
    action: Mapped[AuditAction] = mapped_column(
        Enum(AuditAction, name="audit_action", create_type=True),
        nullable=False,
    )
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[JSONDict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_audit_event__occurred_at", "occurred_at"),
        Index("ix_audit_event__actor_action", "actor_user_id", "action"),
    )
