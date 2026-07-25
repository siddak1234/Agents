"""bootstrap postgres extensions

Revision ID: 0001_bootstrap
Revises:
Create Date: 2026-07-23
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_bootstrap"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PostGIS needs to exist before any Geography column is created.
    for ext in ("uuid-ossp", "pgcrypto", "postgis", "pg_trgm", "btree_gin"):
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{ext}";')


def downgrade() -> None:
    # Do not drop extensions on downgrade — other schemas may share them.
    pass
