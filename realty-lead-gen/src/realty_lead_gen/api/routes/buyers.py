"""Buyer profile endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from realty_lead_gen.api.auth import TokenClaims
from realty_lead_gen.api.deps import get_current_user, get_session
from realty_lead_gen.models.buyer import BuyerProfile
from realty_lead_gen.models.user import User
from realty_lead_gen.schemas.buyer import BuyerProfileCreate, BuyerProfileDTO

router = APIRouter(prefix="/buyer-profiles", tags=["buyer_profiles"])


async def _resolve_user(session: AsyncSession, external_id: str) -> User:
    row = await session.execute(select(User).where(User.external_id == external_id))
    user = row.scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return user


def _to_dto(b: BuyerProfile) -> BuyerProfileDTO:
    return BuyerProfileDTO(
        id=b.id,
        display_name=b.display_name,
        email=b.email,
        phone_e164=b.phone_e164,
        target_cities=list(b.target_cities or []),
        target_postal_codes=list(b.target_postal_codes or []),
        max_price_cents=b.max_price_cents,
        min_price_cents=b.min_price_cents,
        min_bedrooms=b.min_bedrooms,
        min_bathrooms=b.min_bathrooms,
        min_living_area_sqft=b.min_living_area_sqft,
        max_living_area_sqft=b.max_living_area_sqft,
        property_types=list(b.property_types or []),
        must_haves=list(b.must_haves or []),
        nice_to_haves=list(b.nice_to_haves or []),
        deal_breakers=list(b.deal_breakers or []),
        readiness=b.readiness,
        is_active=b.is_active,
    )


@router.get("", response_model=list[BuyerProfileDTO])
async def list_profiles(
    claims: TokenClaims = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[BuyerProfileDTO]:
    user = await _resolve_user(session, claims.sub)
    rows = await session.execute(
        select(BuyerProfile).where(BuyerProfile.user_id == user.id, BuyerProfile.is_active)
    )
    return [_to_dto(b) for b in rows.scalars().all()]


@router.post("", response_model=BuyerProfileDTO, status_code=201)
async def create_profile(
    payload: BuyerProfileCreate,
    claims: TokenClaims = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> BuyerProfileDTO:
    user = await _resolve_user(session, claims.sub)
    row = BuyerProfile(
        user_id=user.id,
        display_name=payload.display_name,
        email=payload.email,
        phone_e164=payload.phone_e164,
        target_cities=payload.target_cities,
        target_postal_codes=payload.target_postal_codes,
        max_price_cents=payload.max_price_cents,
        min_price_cents=payload.min_price_cents,
        min_bedrooms=payload.min_bedrooms,
        min_bathrooms=payload.min_bathrooms,
        min_living_area_sqft=payload.min_living_area_sqft,
        max_living_area_sqft=payload.max_living_area_sqft,
        property_types=payload.property_types,
        must_haves=payload.must_haves,
        nice_to_haves=payload.nice_to_haves,
        deal_breakers=payload.deal_breakers,
        readiness=payload.readiness,
    )
    session.add(row)
    await session.flush()
    return _to_dto(row)


@router.delete("/{profile_id}", status_code=204)
async def deactivate_profile(
    profile_id: uuid.UUID,
    claims: TokenClaims = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    user = await _resolve_user(session, claims.sub)
    row = (
        await session.execute(
            select(BuyerProfile).where(
                BuyerProfile.id == profile_id, BuyerProfile.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    row.is_active = False
