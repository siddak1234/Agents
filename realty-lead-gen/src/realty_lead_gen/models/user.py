"""Users. Auth is bring-your-own (JWT verify only); the frontend / Snoopy
mints tokens. We mirror a subset of the token claims to power local
authorization checks without re-hitting the IDP on every request.

Table name is `app_user` to avoid collision with the reserved word in
some Postgres tooling.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Enum, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from realty_lead_gen.models.base import Base, TimestampMixin, UUIDPKMixin
from realty_lead_gen.utils.jsontypes import JSONDict


class UserRole(StrEnum):
    admin = "admin"
    realtor = "realtor"
    investor = "investor"
    wholesaler = "wholesaler"
    viewer = "viewer"


class User(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "app_user"

    external_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", create_type=True),
        nullable=False,
        default=UserRole.realtor,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attributes: Mapped[JSONDict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (Index("ix_app_user__external_id", "external_id"),)
