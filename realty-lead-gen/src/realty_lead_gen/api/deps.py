"""Dependency-injection functions for FastAPI routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends

from realty_lead_gen.api.auth import TokenClaims, verify_token
from realty_lead_gen.config import Settings, get_settings
from realty_lead_gen.db import session_scope

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_scope() as session:
        yield session


async def get_current_user(
    claims: TokenClaims = Depends(verify_token),
) -> TokenClaims:
    return claims


async def get_settings_dep() -> Settings:
    return get_settings()
