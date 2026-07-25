"""Lead endpoints — the primary UX.

GET /leads?zip=&persona=&min_score=&cursor=&limit=
GET /leads/{id}
POST /leads/{id}/feedback
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from realty_lead_gen.api.auth import TokenClaims
from realty_lead_gen.api.deps import get_current_user, get_session
from realty_lead_gen.api.mappers import property_to_dto
from realty_lead_gen.api.pagination import LeadCursor
from realty_lead_gen.models.deal import DealAnalysis
from realty_lead_gen.models.lead import Lead, LeadFeedback
from realty_lead_gen.models.property import Property
from realty_lead_gen.models.score import Persona, Score
from realty_lead_gen.models.user import User
from realty_lead_gen.schemas.lead import (
    DealAnalysisDTO,
    DealSummaryDTO,
    LeadDetailDTO,
    LeadFeedbackCreate,
    LeadListItemDTO,
    PaginatedLeadsDTO,
    ScoreDTO,
)

router = APIRouter(prefix="/leads", tags=["leads"])


async def _resolve_user(session: AsyncSession, external_id: str) -> User:
    stmt = select(User).where(User.external_id == external_id)
    row = await session.execute(stmt)
    user = row.scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return user


@router.get("", response_model=PaginatedLeadsDTO)
async def list_leads(
    # Keyword-only. FastAPI always invokes an endpoint with keyword
    # arguments built from the resolved dependency graph, so the `*` costs
    # nothing at runtime and buys two things: PLR0917 stops counting eight
    # positional parameters, and nobody can call this function positionally
    # from a test and silently bind `min_score` to the `persona` slot.
    *,
    zip: Annotated[list[str] | None, Query(alias="zip")] = None,
    city: Annotated[list[str] | None, Query(alias="city")] = None,
    persona: Annotated[Persona | None, Query()] = None,
    min_score: Annotated[Decimal, Query(ge=0, le=1)] = Decimal("0"),
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    claims: TokenClaims = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PaginatedLeadsDTO:
    user = await _resolve_user(session, claims.sub)

    conditions = [Lead.user_id == user.id, Lead.score_snapshot >= min_score]
    if persona is not None:
        conditions.append(Lead.persona == persona)
    if zip:
        conditions.append(Property.postal_code.in_(zip))
    if city:
        conditions.append(Property.city.in_(city))

    if cursor is not None:
        # A cursor is client-supplied text. `LeadCursor.decode` normalizes
        # every way it can be wrong into `ValueError` precisely so that a
        # stale bookmark is a 400 here rather than an unhandled exception
        # spending the service's error budget on a bad link.
        try:
            decoded = LeadCursor.decode(cursor)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid cursor") from exc
        conditions.append(
            or_(
                Lead.score_snapshot < decoded.score,
                and_(Lead.score_snapshot == decoded.score, Lead.id < decoded.id),
            )
        )

    stmt = (
        select(Lead)
        .join(Property, Lead.property_id == Property.id)
        .where(and_(*conditions))
        .order_by(Lead.score_snapshot.desc(), Lead.id.desc())
        .limit(limit + 1)
        .options(selectinload(Lead.user))
    )

    rows = (await session.execute(stmt)).scalars().all()
    has_more = len(rows) > limit
    trimmed = rows[:limit]

    # Fetch property + deal summaries in one round-trip
    prop_ids = [r.property_id for r in trimmed]
    prop_map: dict[uuid.UUID, Property] = {}
    deal_map: dict[uuid.UUID, DealAnalysis] = {}
    if prop_ids:
        props = (
            (await session.execute(select(Property).where(Property.id.in_(prop_ids))))
            .scalars()
            .all()
        )
        prop_map = {p.id: p for p in props}

        deal_stmt = (
            select(DealAnalysis)
            .where(DealAnalysis.property_id.in_(prop_ids))
            .order_by(DealAnalysis.property_id, DealAnalysis.analysis_version.desc())
        )
        for d in (await session.execute(deal_stmt)).scalars().all():
            if d.property_id not in deal_map:
                deal_map[d.property_id] = d

    items: list[LeadListItemDTO] = []
    for lead in trimmed:
        prop = prop_map.get(lead.property_id)
        if prop is None:
            continue
        deal = deal_map.get(lead.property_id)
        items.append(
            LeadListItemDTO(
                id=lead.id,
                property=property_to_dto(prop),
                persona=lead.persona,
                status=lead.status,
                score=lead.score_snapshot,
                surfaced_at=lead.surfaced_at,
                deal_summary=(
                    DealSummaryDTO(
                        overall_condition=deal.overall_condition,
                        rehab_low_cents=deal.rehab_low_cents,
                        rehab_high_cents=deal.rehab_high_cents,
                        avm_value_cents=deal.avm_value_cents,
                        avm_confidence=deal.avm_confidence,
                        arv_cents=deal.arv_cents,
                        monthly_rent_cents=deal.monthly_rent_cents,
                        red_flags=list(deal.red_flags or []),
                    )
                    if deal
                    else None
                ),
            )
        )

    next_cursor = (
        LeadCursor(score=trimmed[-1].score_snapshot, id=trimmed[-1].id).encode()
        if has_more and trimmed
        else None
    )

    return PaginatedLeadsDTO(items=items, next_cursor=next_cursor, total_estimate=None)


@router.get("/{lead_id}", response_model=LeadDetailDTO)
async def get_lead(
    lead_id: uuid.UUID,
    claims: TokenClaims = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LeadDetailDTO:
    user = await _resolve_user(session, claims.sub)
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.user_id == user.id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lead not found")

    prop = (
        await session.execute(select(Property).where(Property.id == lead.property_id))
    ).scalar_one()

    deal = (
        await session.execute(
            select(DealAnalysis)
            .where(DealAnalysis.property_id == prop.id)
            .order_by(DealAnalysis.analysis_version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    score = None
    if lead.score_id:
        score = (
            await session.execute(select(Score).where(Score.id == lead.score_id))
        ).scalar_one_or_none()

    # Mark as viewed (idempotent)
    if lead.viewed_at is None:
        lead.viewed_at = datetime.now(UTC)

    return LeadDetailDTO(
        id=lead.id,
        property=property_to_dto(prop),
        persona=lead.persona,
        status=lead.status,
        score=lead.score_snapshot,
        surfaced_at=lead.surfaced_at,
        deal=(
            DealAnalysisDTO(
                id=deal.id,
                analysis_version=deal.analysis_version,
                overall_condition=deal.overall_condition,
                condition_confidence=deal.condition_confidence,
                rehab_low_cents=deal.rehab_low_cents,
                rehab_high_cents=deal.rehab_high_cents,
                rehab_line_items=list(deal.rehab_line_items or []),
                avm_value_cents=deal.avm_value_cents,
                avm_low_cents=deal.avm_low_cents,
                avm_high_cents=deal.avm_high_cents,
                avm_confidence=deal.avm_confidence,
                avm_provider=deal.avm_provider,
                arv_cents=deal.arv_cents,
                arv_confidence=deal.arv_confidence,
                monthly_rent_cents=deal.monthly_rent_cents,
                comps=list(deal.comps or []),
                comps_narrative=deal.comps_narrative,
                red_flags=list(deal.red_flags or []),
                narrative=deal.narrative,
                quality_gate_flags=list(deal.quality_gate_flags or []),
            )
            if deal
            else None
        ),
        score_detail=(
            ScoreDTO(
                persona=score.persona,
                score=score.score,
                confidence=score.confidence,
                components=dict(score.components or {}),
                rationale=score.rationale,
            )
            if score
            else None
        ),
    )


@router.post("/{lead_id}/feedback", status_code=201)
async def create_feedback(
    lead_id: uuid.UUID,
    payload: LeadFeedbackCreate,
    claims: TokenClaims = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    user = await _resolve_user(session, claims.sub)
    lead = (
        await session.execute(select(Lead).where(Lead.id == lead_id, Lead.user_id == user.id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lead not found")

    fb = LeadFeedback(
        lead_id=lead.id,
        user_id=user.id,
        action=payload.action,
        edits=payload.edits,
        note=payload.note,
    )
    session.add(fb)
    return {"status": "recorded"}
