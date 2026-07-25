"""Async SQLAlchemy engine + session management.

We deliberately use SQLAlchemy 2's async engine (`asyncpg` driver) with
a per-request session pattern. The connection pool is sized from
settings; production should tune this alongside the Postgres
`max_connections` cap.

Alembic runs on this same driver — see `alembic/env.py`, which drives
migrations through `AsyncEngine.run_sync` rather than pulling in a second
synchronous Postgres driver just for DDL.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from realty_lead_gen.config import Settings, get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# Module-level lazy singletons, and the `global` statements below are
# deliberate — see the `PLW0603` note in `pyproject.toml`. A connection pool
# must be process-wide (one pool, sized against Postgres `max_connections`)
# and must not be built at import time, or importing any module in this
# package would open sockets — breaking `alembic`, `--help`, and every unit
# test that never touches a database.
_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Return the process-wide async engine, creating it on first call."""
    global _engine
    if _engine is None:
        s = settings or get_settings()
        _engine = create_async_engine(
            s.database_url,
            pool_size=s.database_pool_size,
            max_overflow=s.database_max_overflow,
            pool_pre_ping=True,
            future=True,
            echo=False,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
            class_=AsyncSession,
        )
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Context manager that yields a session and commits/rolls back."""
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Shut down the pool. Called on application shutdown."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
