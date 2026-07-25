"""RESO Web API adapter (Trestle / MLS Grid).

Production path — implement fully once a broker sponsorship is in
place and a Trestle or MLS Grid token has been issued. This module
documents the contract and gives the pipeline a place to plug in.

Reference:
    * RESO Web API spec: https://api.reso.org/
    * Trestle: https://trestle-documentation.corelogic.com/
    * MLS Grid: https://docs.mlsgrid.com/
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


class ResoMlsAdapter:
    name = "reso_mls"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def available(self) -> bool:
        return self._settings.reso_trestle_token is not None

    async def fetch(
        self,
        region: SearchRegion,
        limit: int,
    ) -> AsyncIterator[RawListing]:
        if not self.available:
            logger.info("reso_mls.unavailable", reason="no_trestle_token")
            return
        # NOTE: production implementation should:
        #   1. GET {base}/trestle/odata/Property?$filter=PostalCode eq '...' & ...
        #      with OData $top pagination up to ``limit``.
        #   2. Map RESO Data Dictionary fields into RawListing.
        #   3. Track ModificationTimestamp for incremental deltas
        #      (store last-seen ts per MLS in a small state table).
        #   4. Honor MLS-specific media licensing rules (don't republish photos).
        logger.warning("reso_mls.not_implemented", region=region)
        return
        yield  # pragma: no cover — makes this an async generator
