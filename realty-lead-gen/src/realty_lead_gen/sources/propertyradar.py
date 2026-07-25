"""PropertyRadar adapter — distressed / off-market listings and signals.

PropertyRadar is the recommended production source for tax-delinquent,
NOD, high-equity, and absentee-owner lead lists. API is available on
the Business tier ($599/mo) as of 2026 research.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from realty_lead_gen.config import Settings, get_settings
from realty_lead_gen.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from realty_lead_gen.schemas.listing import RawListing
    from realty_lead_gen.sources.base import SearchRegion

logger = get_logger(__name__)


class PropertyRadarAdapter:
    name = "propertyradar"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def available(self) -> bool:
        return self._settings.propertyradar_api_token is not None

    async def fetch(
        self,
        region: SearchRegion,
        limit: int,
    ) -> AsyncIterator[RawListing]:
        if not self.available:
            logger.info("propertyradar.unavailable")
            return
        # Production implementation: POST /v1/properties with filters composed
        # from `region` + criteria (Equity, LastMarketPrice, ForeclosureStage,
        # OccupancyStatus). Yield each as RawListing with intent=for_sale or
        # =other and attach Signal records via a side channel (see jobs.enrich).
        logger.warning("propertyradar.not_implemented")
        return
        yield  # pragma: no cover
