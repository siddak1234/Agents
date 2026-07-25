"""FSBO / auction adapter — Zillow FSBO, ForSaleByOwner.com, Auction.com.

Scaffold only. Each site has its own auth story (public), rate limit,
and anti-bot posture. Recommend: route FSBO scraping through Bright
Data Web Unlocker to survive Cloudflare / PerimeterX.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from realty_lead_gen.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from realty_lead_gen.schemas.listing import RawListing
    from realty_lead_gen.sources.base import SearchRegion

logger = get_logger(__name__)


class FsboAdapter:
    name = "fsbo"
    available: bool = False

    async def fetch(
        self,
        region: SearchRegion,
        limit: int,
    ) -> AsyncIterator[RawListing]:
        logger.info("fsbo.deferred")
        return
        yield  # pragma: no cover
