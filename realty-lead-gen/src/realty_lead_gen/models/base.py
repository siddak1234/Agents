"""Declarative base + reusable mixins.

Conventions we hold every table to:
    * Primary key: UUID v7-shaped (uuid_generate_v4 fallback at DB level;
      application-generated UUIDs use ``uuid.uuid7()`` where available for
      time-sortable inserts — Python 3.14 will make this native. Until
      then application code uses ``uuid.uuid4()``.
    * Timestamps: ``created_at`` and ``updated_at`` with ``timezone=True``.
    * Soft delete: opt-in via ``SoftDeleteMixin``; most tables are append-only.
    * Naming: snake_case tables, singular nouns (``property``, ``listing``).
      Cross-table constraint names use the naming convention below so
      Alembic's autogenerate produces stable migrations.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Standardized constraint naming — required for Alembic autogenerate stability.
# See https://alembic.sqlalchemy.org/en/latest/naming.html
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s__%(column_0_name)s",
    "ck": "ck_%(table_name)s__%(constraint_name)s",
    "fk": "fk_%(table_name)s__%(column_0_name)s__%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base for all ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPKMixin:
    """UUID primary key."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    """created_at / updated_at, both timezone-aware."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        default=lambda: datetime.now(UTC),
    )
