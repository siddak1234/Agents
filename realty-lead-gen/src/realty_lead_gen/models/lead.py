"""Leads — the user-facing surface.

A Lead is a materialized (property, persona, user) triple that survived
scoring above a per-persona threshold. Feedback on leads is stored
separately for continuous evaluation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from realty_lead_gen.models.base import Base, TimestampMixin, UUIDPKMixin
from realty_lead_gen.models.score import Persona
from realty_lead_gen.utils.jsontypes import JSONDict

if TYPE_CHECKING:
    from realty_lead_gen.models.user import User


class LeadStatus(StrEnum):
    new = "new"
    viewed = "viewed"
    contacted = "contacted"
    under_contract = "under_contract"
    closed_won = "closed_won"
    closed_lost = "closed_lost"
    dismissed = "dismissed"


class Lead(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "lead"

    property_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("property.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"),
        nullable=False,
    )
    persona: Mapped[Persona] = mapped_column(
        Enum(Persona, name="persona", create_type=False),
        nullable=False,
    )
    score_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("score.id", ondelete="SET NULL"))
    score_snapshot: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)

    status: Mapped[LeadStatus] = mapped_column(
        Enum(LeadStatus, name="lead_status", create_type=True),
        nullable=False,
        default=LeadStatus.new,
    )
    surfaced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    saved_search_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("saved_search.id", ondelete="SET NULL")
    )

    # Ranking blob (why we ranked it here today for this user)
    rank_meta: Mapped[JSONDict] = mapped_column(JSONB, nullable=False, default=dict)

    user: Mapped[User] = relationship(lazy="joined")
    feedback: Mapped[list[LeadFeedback]] = relationship(back_populates="lead", lazy="raise")

    __table_args__ = (
        Index("uq_lead__property_user_persona", "property_id", "user_id", "persona", unique=True),
        Index("ix_lead__user_status_surfaced", "user_id", "status", "surfaced_at"),
        Index("ix_lead__user_score_desc", "user_id", "score_snapshot"),
    )


class LeadFeedbackAction(StrEnum):
    accepted = "accepted"
    edited = "edited"
    dismissed = "dismissed"
    reported_inaccurate = "reported_inaccurate"


class LeadFeedback(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "lead_feedback"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lead.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[LeadFeedbackAction] = mapped_column(
        Enum(LeadFeedbackAction, name="lead_feedback_action", create_type=True),
        nullable=False,
    )
    # Which fields were disputed / edited, if any
    edits: Mapped[JSONDict] = mapped_column(JSONB, nullable=False, default=dict)
    note: Mapped[str | None] = mapped_column(String(2048))

    lead: Mapped[Lead] = relationship(back_populates="feedback", lazy="joined")

    __table_args__ = (Index("ix_lead_feedback__lead", "lead_id"),)
