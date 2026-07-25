"""Shared test fixtures.

Unit tests need nothing but the interpreter, so `pytest -m unit` never
touches this module's database machinery — the Postgres fixtures are
function/session-scoped and only materialize when an integration test
actually asks for one.

Two decisions here are load-bearing:

1. **The schema is built by Alembic, not by `Base.metadata.create_all`.**
   `create_all` reads the same model metadata the tests import, so it can
   only ever agree with itself: a migration that fails to apply, or that
   drifts from the models, passes every test. Running `alembic upgrade
   head` means the integration suite fails for the same reason production
   would. (This is not hypothetical — building the suite this way is what
   surfaced a missing `DROP TYPE` in the downgrade path.)

2. **Each test runs inside a transaction that is rolled back.** Migrating
   once per session and rolling back per test is both faster and stricter
   than recreating the schema: tests cannot leak state into each other,
   and a test that depends on another's rows fails immediately.

Database selection order: `TEST_DATABASE_URL`, then `DATABASE_URL`, then a
testcontainers-managed PostGIS container. CI sets `DATABASE_URL` to its
service container, so it takes the first branch and never pays for Docker
twice; a laptop with a local Postgres does the same. The container is the
fallback, not the default.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from realty_lead_gen.config import get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure tests don't inherit real credentials."""
    for var in (
        "ANTHROPIC_API_KEY",
        "RAPIDAPI_KEY",
        "RENTCAST_API_KEY",
        "RESO_TRESTLE_TOKEN",
        "PROPERTYRADAR_API_TOKEN",
        "BATCHSKIPTRACING_API_KEY",
        "MLS_GRID_TOKEN",
        "OUTBOX_WEBHOOK_URL",
        "OUTBOX_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)
    # Test defaults. The secret is >= 32 bytes on purpose: PyJWT emits an
    # `InsecureKeyLengthWarning` for anything shorter under HS256 (RFC 7518
    # §3.2 sets the floor at the hash output size), and `filterwarnings =
    # ["error"]` turns that into a failure the moment a test mints a token.
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("JWT_HS_SECRET", "test-secret-not-a-secret-32bytes-min")


def _normalize_async_dsn(dsn: str) -> str:
    """Force the asyncpg driver form regardless of how the DSN arrived."""
    if "+asyncpg" in dsn:
        return dsn
    if dsn.startswith("postgresql+"):
        # The scheme is discarded on purpose: this branch has already
        # established it is a `postgresql+<driver>://` form, and the whole
        # point is to replace whatever driver it named with asyncpg.
        _, _, rest = dsn.partition("://")
        return f"postgresql+asyncpg://{rest}"
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    if dsn.startswith("postgres://"):
        return dsn.replace("postgres://", "postgresql+asyncpg://", 1)
    return dsn


@pytest.fixture(scope="session")
def postgres_dsn() -> Iterator[str]:
    """An asyncpg DSN for a live PostGIS-enabled Postgres.

    Prefers an externally supplied database so the suite runs identically
    in CI (service container), on a laptop with a local server, and in a
    sandbox without a Docker socket. Falls back to testcontainers only
    when nothing was supplied.
    """
    supplied = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if supplied:
        yield _normalize_async_dsn(supplied)
        return

    try:
        # Local by necessity, not by style: testcontainers is an optional
        # extra, and this whole branch exists to degrade to `pytest.skip`
        # when it is absent. A module-level import would make the import
        # error a collection error for the entire suite, including the unit
        # tests that never touch a database.
        from testcontainers.postgres import PostgresContainer  # noqa: PLC0415
    except ImportError:  # pragma: no cover - environment-dependent
        pytest.skip("No TEST_DATABASE_URL/DATABASE_URL set and testcontainers missing")

    try:
        with PostgresContainer("postgis/postgis:17-3.5", driver="asyncpg") as pg:
            yield _normalize_async_dsn(pg.get_connection_url())
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"Could not start a Postgres container: {exc}")


@pytest.fixture(scope="session")
def _migrated_dsn(postgres_dsn: str) -> Iterator[str]:
    """Bring the target database to `head` using the real migration chain."""
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = postgres_dsn

    # `get_settings` is lru_cached; without this the migration and the
    # session could disagree about which database they are talking to.
    # (Import position is irrelevant precisely *because* of this call — the
    # cache, not the import, is what pins the DSN.)
    get_settings.cache_clear()

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    # Revision scripts are already formatted in-repo; re-running the hooks
    # during a test would be pure noise.
    cfg.set_section_option("post_write_hooks", "hooks", "")
    try:
        command.upgrade(cfg, "head")
        yield postgres_dsn
    finally:
        get_settings.cache_clear()
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest_asyncio.fixture()
async def _engine(_migrated_dsn: str) -> AsyncIterator[AsyncEngine]:
    """A fresh engine per test.

    Deliberately function-scoped despite the cost. asyncpg binds every
    connection to the event loop that created it, and pytest-asyncio gives
    each test its own loop, so a session-scoped engine hands the second
    test a connection owned by a dead loop — which surfaces as the
    thoroughly unhelpful ``another operation is in progress``. NullPool
    means no connection outlives the test that opened it.
    """
    engine = create_async_engine(_migrated_dsn, poolclass=NullPool, future=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture()
async def _integration_session(_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session whose writes are always rolled back.

    The session is bound to an already-open connection-level transaction.
    Anything the test commits lands in a SAVEPOINT inside it, so committing
    is legal (code under test can call ``session.commit()`` normally) but
    nothing survives the test. Schema state is therefore migrated once for
    the whole run while row state is isolated per test.
    """
    async with _engine.connect() as connection:
        outer = await connection.begin()
        maker = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            class_=AsyncSession,
            join_transaction_mode="create_savepoint",
        )
        session = maker()
        try:
            yield session
        finally:
            await session.close()
            if outer.is_active:
                await outer.rollback()
