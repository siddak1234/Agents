"""Daily cron: enumerate active saved-search regions and enqueue ingestion."""

from __future__ import annotations

from typing import Any

from realty_lead_gen.db import session_scope
from realty_lead_gen.logging import get_logger
from realty_lead_gen.pipeline.orchestrator import load_active_saved_search_regions

logger = get_logger(__name__)


async def daily_sweep_job(ctx: dict[str, Any]) -> dict[str, Any]:
    pool = ctx["redis"]
    async with session_scope() as session:
        regions = await load_active_saved_search_regions(session)
    for region in regions:
        await pool.enqueue_job(
            "ingest_region_job",
            postal_codes=list(region.postal_codes),
            cities=list(region.cities),
            regions=list(region.regions),
        )
    logger.info("daily_sweep.enqueued", region_count=len(regions))
    return {"regions_enqueued": len(regions)}
