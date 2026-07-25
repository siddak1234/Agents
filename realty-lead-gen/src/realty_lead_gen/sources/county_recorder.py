"""County recorder scraper — public records path.

The recorder + assessor scrape stack is what PropStream / BatchLeads
built their moat around. Implementing across all ~3,100 US counties is
a $500k+ engineering project; we defer to PropertyRadar / ATTOM at MVP
and revisit per-county scrapers only where we consistently see high
lead value (e.g. Los Angeles, Cook, Miami-Dade).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from realty_lead_gen.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from realty_lead_gen.schemas.listing import RawListing
    from realty_lead_gen.sources.base import SearchRegion

logger = get_logger(__name__)


class CountyRecorderAdapter:
    name = "county_recorder"
    available: bool = False  # opt-in per-county; nothing wired at MVP

    async def fetch(
        self,
        region: SearchRegion,
        limit: int,
    ) -> AsyncIterator[RawListing]:
        logger.info("county_recorder.deferred")
        return
        yield  # pragma: no cover
