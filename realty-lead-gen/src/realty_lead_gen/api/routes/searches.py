"""Saved-search endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from realty_lead_gen.api.auth import TokenClaims
from realty_lead_gen.api.deps import get_current_user, get_session
from realty_lead_gen.models.buyer import SavedSearch
from realty_lead_gen.models.user import User
from realty_lead_gen.schemas.buyer import SavedSearchCreate, SavedSearchDTO

router = APIRouter(prefix="/searches", tags=["searches"])


async def _resolve_user(session: AsyncSession, external_id: str) -> User:
    row = await session.execute(select(User).where(User.external_id == external_id))
    user = row.scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return user


def _to_dto(s: SavedSearch) -> SavedSearchDTO:
    return SavedSearchDTO(
        id=s.id,
        name=s.name,
        persona=s.persona,
        postal_codes=list(s.postal_codes or []),
        cities=list(s.cities or []),
        regions=list(s.regions or []),
        min_score=s.min_score,
        criteria=dict(s.criteria or {}),
        is_active=s.is_active,
    )


@router.get("", response_model=list[SavedSearchDTO])
async def list_searches(
    claims: TokenClaims = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[SavedSearchDTO]:
    user = await _resolve_user(session, claims.sub)
    rows = await session.execute(
        select(SavedSearch).where(SavedSearch.user_id == user.id, SavedSearch.is_active)
    )
    return [_to_dto(s) for s in rows.scalars().all()]


@router.post("", response_model=SavedSearchDTO, status_code=201)
async def create_search(
    payload: SavedSearchCreate,
    claims: TokenClaims = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SavedSearchDTO:
    user = await _resolve_user(session, claims.sub)
    if not (payload.postal_codes or payload.cities or payload.regions):
        raise HTTPException(422, "provide at least one of postal_codes, cities, regions")
    row = SavedSearch(
        user_id=user.id,
        name=payload.name,
        persona=payload.persona,
        postal_codes=payload.postal_codes,
        cities=payload.cities,
        regions=payload.regions,
        min_score=payload.min_score,
        criteria=payload.criteria,
    )
    session.add(row)
    await session.flush()
    return _to_dto(row)


@router.delete("/{search_id}", status_code=204)
async def deactivate_search(
    search_id: uuid.UUID,
    claims: TokenClaims = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    user = await _resolve_user(session, claims.sub)
    row = (
        await session.execute(
            select(SavedSearch).where(SavedSearch.id == search_id, SavedSearch.user_id == user.id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    row.is_active = False
