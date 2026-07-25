"""Sanity check: the schema builds against a real Postgres 17 + PostGIS."""

from __future__ import annotations

import pytest
from sqlalchemy import text


@pytest.mark.integration
async def test_schema_creates(_integration_session) -> None:
    """The fixture builds the whole schema; assert a couple of tables exist."""
    result = await _integration_session.execute(
        text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
    )
    count = result.scalar_one()
    assert count > 10  # we have well over 10 tables


@pytest.mark.integration
async def test_postgis_extension(_integration_session) -> None:
    result = await _integration_session.execute(text("SELECT PostGIS_Version()"))
    assert result.scalar_one() is not None
