"""Ingest one region using every available source adapter.

Enqueues per-property enrichment jobs for anything new or materially
changed. Enrichment is the only expensive step in the system (vision LLM
calls plus paid AVM/comps lookups), so the fan-out is deliberately narrow:
we enqueue exactly the property ids the orchestrator reports as touched,
and we key each job so that repeated ingests inside the same coalescing
window collapse to a single enrichment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from realty_lead_gen.db import session_scope
from realty_lead_gen.logging import get_logger
from realty_lead_gen.pipeline.orchestrator import Orchestrator
from realty_lead_gen.sources import all_available_adapters
from realty_lead_gen.sources.base import SearchRegion

if TYPE_CHECKING:
    import uuid

logger = get_logger(__name__)

#: Two ingests of the same property inside this window produce one
#: enrichment. Sized to the cadence at which listing data actually moves:
#: portals refresh on the order of hours, not minutes.
ENRICH_COALESCE_WINDOW_HOURS = 6


def enrich_job_id(property_id: uuid.UUID, now: datetime | None = None) -> str:
    """Deterministic arq job id used to coalesce duplicate enrichments.

    arq refuses to enqueue a job whose id is already queued or whose result
    is still retained, so bucketing the timestamp gives us idempotency
    without a separate dedup table. The bucket is floored, not rounded, so
    the id is stable for every call inside the window.
    """
    stamp = now or datetime.now(UTC)
    bucket = stamp.replace(
        hour=(stamp.hour // ENRICH_COALESCE_WINDOW_HOURS) * ENRICH_COALESCE_WINDOW_HOURS,
        minute=0,
        second=0,
        microsecond=0,
    )
    return f"enrich:{property_id}:{bucket.strftime('%Y%m%dT%H')}"


async def ingest_region_job(
    ctx: dict[str, Any],
    postal_codes: list[str] | None = None,
    cities: list[str] | None = None,
    regions: list[str] | None = None,
    limit_per_adapter: int = 200,
) -> dict[str, Any]:
    region = SearchRegion(
        postal_codes=tuple(postal_codes or ()),
        cities=tuple(cities or ()),
        regions=tuple(regions or ()),
    )
    if region.is_empty():
        logger.warning("ingest.empty_region")
        return {"listings_seen": 0}

    adapters = all_available_adapters()
    async with session_scope() as session:
        orch = Orchestrator(session, adapters)
        report = await orch.ingest_region(region, limit_per_adapter)
    # session_scope has committed by here, so every id below addresses a row
    # that is durable and visible to the worker that picks the job up.

    enqueued = 0
    coalesced = 0
    pool = ctx.get("redis")
    if pool is not None:
        for property_id in report.touched_property_ids:
            job = await pool.enqueue_job(
                "enrich_property_job",
                property_id=str(property_id),
                _job_id=enrich_job_id(property_id),
            )
            # arq returns None when the job id already exists — that is the
            # coalesce path, not an error.
            if job is None:
                coalesced += 1
            else:
                enqueued += 1
    else:
        logger.warning("ingest.no_redis_pool", touched=len(report.touched_property_ids))

    logger.info(
        "ingest.complete",
        listings_seen=report.listings_seen,
        touched=len(report.touched_property_ids),
        enqueued=enqueued,
        coalesced=coalesced,
    )

    return {
        "listings_seen": report.listings_seen,
        "properties_created": report.properties_created,
        "properties_updated": report.properties_updated,
        "listings_upserted": report.listings_upserted,
        "photos_added": report.photos_added,
        "enrichment_enqueued": enqueued,
        "enrichment_coalesced": coalesced,
        "errors": report.errors,
    }
