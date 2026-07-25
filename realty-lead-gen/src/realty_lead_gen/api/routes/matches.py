"""Buyer <-> property matching endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from realty_lead_gen.api.auth import TokenClaims
from realty_lead_gen.api.deps import get_current_user, get_session
from realty_lead_gen.matching.buyer_intent import BuyerMatcher
from realty_lead_gen.models.buyer import BuyerProfile
from realty_lead_gen.models.property import Property, PropertySnapshot
from realty_lead_gen.models.user import User
from realty_lead_gen.utils.jsontypes import JSONDict

router = APIRouter(prefix="/matches", tags=["matches"])


async def _resolve_user(session: AsyncSession, external_id: str) -> User:
    row = await session.execute(select(User).where(User.external_id == external_id))
    user = row.scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return user


@router.get("/property/{property_id}", response_model=list[JSONDict])
async def matches_for_property(
    property_id: uuid.UUID,
    claims: TokenClaims = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[JSONDict]:
    """Find this user's buyer profiles that match the given property."""
    user = await _resolve_user(session, claims.sub)
    prop = (
        await session.execute(select(Property).where(Property.id == property_id))
    ).scalar_one_or_none()
    if prop is None:
        raise HTTPException(404, "property not found")

    latest_price_row = (
        await session.execute(
            select(PropertySnapshot.list_price_cents)
            .where(PropertySnapshot.property_id == prop.id)
            .order_by(PropertySnapshot.observed_at.desc())
            .limit(1)
        )
    ).first()
    latest_price = latest_price_row[0] if latest_price_row else None

    profiles = (
        (
            await session.execute(
                select(BuyerProfile).where(BuyerProfile.user_id == user.id, BuyerProfile.is_active)
            )
        )
        .scalars()
        .all()
    )

    matcher = BuyerMatcher()
    matches = matcher.match(prop, latest_price, list(profiles))
    return [
        {
            "profile_id": m.profile_id,
            "hits": m.hard_hits,
            "total": m.hard_total,
            "reasons": m.reasons,
        }
        for m in matches
    ]
