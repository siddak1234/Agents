"""Per-property enrichment: photo grading + valuation + comps + signals + deal analysis."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from realty_lead_gen.agents.claude_client import ClaudeClient
from realty_lead_gen.agents.photo_grader import PhotoGrader
from realty_lead_gen.config import get_settings
from realty_lead_gen.db import session_scope
from realty_lead_gen.enrichment.comps import CompsRetrieval
from realty_lead_gen.enrichment.photos import PhotoEnrichmentStep
from realty_lead_gen.enrichment.signals import SignalDetectionStep
from realty_lead_gen.enrichment.valuation import ValuationStep
from realty_lead_gen.logging import get_logger
from realty_lead_gen.models.deal import DealAnalysis
from realty_lead_gen.models.enrichment import EnrichmentKind, EnrichmentRun, RunStatus
from realty_lead_gen.models.photo import Photo, PhotoAnalysis, UADCondition
from realty_lead_gen.models.property import Property, PropertySnapshot
from realty_lead_gen.models.signal import Signal
from realty_lead_gen.utils.addr import NormalizedAddress, compose_display_address

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


async def enrich_property_job(ctx: dict[str, Any], property_id: str) -> dict[str, Any]:
    settings = get_settings()
    prop_uuid = uuid.UUID(property_id)

    grader = PhotoGrader(claude=ClaudeClient(settings), settings=settings)
    photo_step = PhotoEnrichmentStep(grader)
    valuation = ValuationStep(settings)
    comps = CompsRetrieval(settings)
    signal_step = SignalDetectionStep()

    result_summary: dict[str, Any] = {"property_id": property_id, "steps": {}}

    async with session_scope() as session:
        prop = (
            await session.execute(select(Property).where(Property.id == prop_uuid))
        ).scalar_one_or_none()
        if prop is None:
            return {"error": "property not found", "property_id": property_id}

        display_address = compose_display_address(
            NormalizedAddress(
                street_number=prop.street_number or "",
                street_name=prop.street_name,
                unit=prop.unit or "",
                city=prop.city,
                state=prop.state,
                postal_code=prop.postal_code,
            )
        )

        # --- Photos --------------------------------------------------------
        photos = (
            (await session.execute(select(Photo).where(Photo.property_id == prop_uuid)))
            .scalars()
            .all()
        )
        photo_urls = [p.url for p in photos][:32]  # cap: don't burn budget on huge sets
        photo_result = None
        if grader._claude.available and photo_urls:
            photo_result = await photo_step.run(photo_urls)
            for p, batch_result in zip(photos, photo_result.per_photo_analyses, strict=False):
                session.add(
                    PhotoAnalysis(
                        photo_id=p.id,
                        model_id=photo_result.model_id,
                        prompt_version=photo_result.prompt_version,
                        analyzed_at=datetime.now(UTC),
                        condition=UADCondition(batch_result.get("condition", "NOT_VISIBLE")),
                        confidence=Decimal(str(batch_result.get("confidence", 0.0))),
                        findings=batch_result.get("systems", []),
                        observations=batch_result.get("notes"),
                        red_flags=[],
                        input_tokens=batch_result.get("input_tokens"),
                        output_tokens=batch_result.get("output_tokens"),
                        cost_usd_micros=batch_result.get("cost_usd_micros"),
                    )
                )
            result_summary["steps"]["photos"] = {
                "cost_usd_micros": photo_result.total_cost_usd_micros,
                "count": len(photos),
            }

        # --- AVM -----------------------------------------------------------
        valuation_result = await valuation.value(display_address)
        rent_result = await valuation.rent(display_address)
        _record_run(session, prop_uuid, EnrichmentKind.avm_valuation, "rentcast", valuation_result)

        # --- Comps ---------------------------------------------------------
        comps_result = None
        if comps.available:
            candidates = await comps.fetch_candidates(display_address)
            comps_result = await comps.rerank(display_address, "Subject property", candidates)
            _record_run(session, prop_uuid, EnrichmentKind.comps, "rentcast+claude", comps_result)

        # --- Signals -------------------------------------------------------
        snapshots = (
            (
                await session.execute(
                    select(PropertySnapshot).where(PropertySnapshot.property_id == prop_uuid)
                )
            )
            .scalars()
            .all()
        )
        derived_signals = signal_step.compute(list(snapshots))
        for s in derived_signals:
            session.add(
                Signal(
                    property_id=prop_uuid,
                    kind=s.kind,
                    observed_on=s.observed_on,
                    strength=s.strength,
                    source=s.source,
                    payload=s.payload,
                )
            )

        # --- Deal analysis -------------------------------------------------
        avm_value = valuation_result.avm_value_cents if valuation_result else None
        avm_low = valuation_result.avm_low_cents if valuation_result else None
        avm_high = valuation_result.avm_high_cents if valuation_result else None
        arv_cents = None
        if comps_result and comps_result.comps:
            close_prices = [c.close_price_cents for c in comps_result.comps if c.close_price_cents]
            if close_prices:
                arv_cents = int(sum(close_prices) / len(close_prices))

        deal = DealAnalysis(
            property_id=prop_uuid,
            analysis_version=1,
            model_id=(photo_result.model_id if photo_result else "n/a"),
            prompt_version=(photo_result.prompt_version if photo_result else "n/a"),
            overall_condition=(photo_result.overall_condition if photo_result else None),
            condition_confidence=(
                Decimal(str(photo_result.overall_confidence)) if photo_result else None
            ),
            rehab_low_cents=(photo_result.rehab_low_cents if photo_result else None),
            rehab_high_cents=(photo_result.rehab_high_cents if photo_result else None),
            rehab_line_items=(photo_result.line_items if photo_result else []),
            avm_value_cents=avm_value,
            avm_low_cents=avm_low,
            avm_high_cents=avm_high,
            avm_provider=("rentcast" if valuation_result else None),
            arv_cents=arv_cents,
            monthly_rent_cents=(rent_result.monthly_rent_cents if rent_result else None),
            comps=(
                [
                    {
                        "address": c.address,
                        "price_cents": c.close_price_cents,
                        "distance_miles": c.distance_miles,
                        "sqft": c.living_area_sqft,
                    }
                    for c in comps_result.comps
                ]
                if comps_result
                else []
            ),
            comps_narrative=(comps_result.narrative if comps_result else None),
            red_flags=(photo_result.red_flags if photo_result else []),
            quality_gate_flags=[],
        )
        session.add(deal)

    # After enrich, fan out scoring
    pool = ctx.get("redis")
    if pool is not None:
        await pool.enqueue_job("score_property_job", property_id=property_id)

    return result_summary


def _record_run(
    session: AsyncSession, property_id: uuid.UUID, kind: EnrichmentKind, provider: str, result: Any
) -> None:
    now = datetime.now(UTC)
    session.add(
        EnrichmentRun(
            property_id=property_id,
            kind=kind,
            status=RunStatus.succeeded
            if result is not None
            else RunStatus.skipped_provider_missing,
            provider=provider,
            idempotency_key=f"{property_id}:{kind.value}:{now.isoformat()}",
            started_at=now,
            finished_at=now,
            duration_ms=0,
            input_summary={},
            output_summary={"ok": bool(result)},
        )
    )
