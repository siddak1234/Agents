"""Alembic environment.

Runs migrations on the *same* async engine the application uses. The
conventional Alembic template is synchronous and expects a psycopg URL,
which would mean shipping a second Postgres driver whose only job is
schema changes — a driver that then diverges from the one production
actually runs on (different type codecs, different server-side prepared
statement behaviour). Using ``AsyncEngine`` + ``connection.run_sync``
keeps exactly one driver in the dependency set and guarantees the DDL
path and the query path see the same server.

Offline mode still emits plain SQL and needs no driver at all.

Autogenerate notes:
    * ``compare_type`` and ``compare_server_default`` are on, so a type
      widening in a model shows up as a diff instead of silently drifting.
    * PostGIS installs ``spatial_ref_sys`` into the target schema, and
      GeoAlchemy2 registers management triggers. Both are excluded so
      autogenerate does not try to drop the extension's own objects.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import TYPE_CHECKING, Any

from alembic import context
from geoalchemy2 import alembic_helpers
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from realty_lead_gen.config import get_settings
from realty_lead_gen.models import Base

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def include_object(
    obj: Any,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: Any,
) -> bool:
    """Filter out objects PostGIS owns rather than us.

    Delegates to GeoAlchemy2's own table-name filter (it knows the full
    list across PostGIS/SpatiaLite/GeoPackage) and then adds the spatial
    indexes PostGIS creates behind our back, which are reflected but have
    no counterpart in our metadata — without this, every autogenerate run
    would propose dropping them.
    """
    if not alembic_helpers.include_object(obj, name, type_, reflected, compare_to):
        return False
    return not (type_ == "index" and reflected and bool(name) and name.startswith("idx_"))


def _configure(**kwargs: Any) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
        # Without this, a Geography column renders as a bare
        # `geoalchemy2.types.Geography(...)` reference with no matching
        # import, and the migration dies with NameError on first run.
        render_item=alembic_helpers.render_item,
        render_as_batch=False,
        **kwargs,
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting to a database."""
    _configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(_run_sync)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
