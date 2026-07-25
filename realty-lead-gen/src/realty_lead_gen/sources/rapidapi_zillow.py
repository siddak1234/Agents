"""RapidAPI Zillow endpoint adapter — reference / prototype.

Uses the community-maintained ``zillow-com1`` RapidAPI endpoint.

Legal / operational notes (from research):
    * These endpoints scrape Zillow behind the scenes and are brittle:
      individual providers get blocked and disappear on quarterly
      timescales. Do NOT ship this to production; use it only for demo
      + smoke tests. Production runs on RESO/MLS-Grid (licensed) plus
      Bright Data unlocker for logged-out portal pages.
    * Zillow's ToS prohibits scraping for logged-in users; Meta v.
      Bright Data (N.D. Cal. 2024) supports the position that ToS does
      not bind logged-out requesters, but Zillow's copyright over
      compiled data and photos is not neutralized. Never republish
      photos, agent remarks, or bulk compilations sourced from Zillow.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from realty_lead_gen.config import Settings, get_settings
from realty_lead_gen.logging import get_logger
from realty_lead_gen.models.listing import ListingIntent, ListingStatus
from realty_lead_gen.models.property import PropertyType, SnapshotSource
from realty_lead_gen.schemas.listing import PhotoDTO, RawListing
from realty_lead_gen.utils.http import raise_for_vendor_status
from realty_lead_gen.utils.money import dollars_to_cents
from realty_lead_gen.utils.retry import PermanentError, TransientError, default_retry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from realty_lead_gen.sources.base import SearchRegion

logger = get_logger(__name__)


_ZILLOW_STATUS_MAP: dict[str, ListingStatus] = {
    "FOR_SALE": ListingStatus.active,
    "PENDING": ListingStatus.pending,
    "SOLD": ListingStatus.sold,
    "COMING_SOON": ListingStatus.coming_soon,
    "OTHER": ListingStatus.unknown,
}

_ZILLOW_TYPE_MAP: dict[str, PropertyType] = {
    "SINGLE_FAMILY": PropertyType.single_family,
    "CONDO": PropertyType.condo,
    "TOWNHOUSE": PropertyType.townhouse,
    "MULTI_FAMILY": PropertyType.multi_family_2_4,
    "MANUFACTURED": PropertyType.manufactured,
    "LOT": PropertyType.land,
}


class RapidApiZillowAdapter:
    name = "rapidapi_zillow"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._api_key = self._settings.rapidapi_key
        self._host = self._settings.rapidapi_zillow_host
        self._client: httpx.AsyncClient | None = None

    @property
    def available(self) -> bool:
        return self._api_key is not None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=f"https://{self._host}",
                timeout=httpx.Timeout(15.0, connect=5.0),
                headers={
                    "x-rapidapi-key": self._api_key.get_secret_value() if self._api_key else "",
                    "x-rapidapi-host": self._host,
                    "accept": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        client = await self._get_client()
        async for attempt in default_retry():
            with attempt:
                try:
                    resp = await client.get(path, params=params)
                except httpx.TransportError as e:
                    raise TransientError(str(e)) from e
                raise_for_vendor_status(resp, vendor=self.name)
                data: dict[str, Any] = resp.json()
                return data
        raise RuntimeError("unreachable")

    async def fetch(
        self,
        region: SearchRegion,
        limit: int,
    ) -> AsyncIterator[RawListing]:
        if not self.available:
            logger.info("rapidapi_zillow.unavailable", reason="no_api_key")
            return

        # We only support postal-code and city search here — the endpoint's
        # geographic query mode.
        queries: list[str] = list(region.postal_codes) + list(region.cities)
        if not queries:
            logger.info("rapidapi_zillow.no_query", reason="empty_region")
            return

        remaining = limit
        for query in queries:
            if remaining <= 0:
                break
            try:
                data = await self._request(
                    "/propertyExtendedSearch",
                    {"location": query, "home_type": "Houses", "status_type": "ForSale"},
                )
            except PermanentError as e:
                # TRY400 suppressed: PermanentError is self-describing; see its docstring.
                logger.error("rapidapi_zillow.permanent", query=query, error=str(e))  # noqa: TRY400
                continue
            props: list[dict[str, Any]] = data.get("props") or []
            for prop in props:
                if remaining <= 0:
                    break
                raw = self._to_raw_listing(prop)
                if raw is not None:
                    remaining -= 1
                    yield raw

    def _to_raw_listing(self, prop: dict[str, Any]) -> RawListing | None:
        """Map Zillow property record to canonical RawListing.

        Skips records missing enough to identify a property.
        """
        try:
            address = str(prop.get("address") or "").strip()
            city = str(prop.get("addressCity") or prop.get("city") or "").strip()
            state = str(prop.get("addressState") or prop.get("state") or "").strip()[:2]
            postal = str(prop.get("addressZipcode") or prop.get("zipcode") or "").strip()[:5]
            if not (address and city and state and postal):
                return None

            zpid = str(prop.get("zpid") or "").strip()
            if not zpid:
                return None

            price = prop.get("price")
            list_price_cents = dollars_to_cents(price) if isinstance(price, (int, float)) else None

            status_str = str(prop.get("listingStatus") or "OTHER").upper()
            status = _ZILLOW_STATUS_MAP.get(status_str, ListingStatus.unknown)

            ptype_str = str(prop.get("propertyType") or "").upper().replace(" ", "_")
            property_type = _ZILLOW_TYPE_MAP.get(ptype_str, PropertyType.single_family)

            photos_raw = prop.get("carouselPhotos") or []
            photos = [
                PhotoDTO(url=p["url"], order_index=i)
                for i, p in enumerate(photos_raw)
                if isinstance(p, dict) and p.get("url")
            ]

            return RawListing(
                source=SnapshotSource.zillow,
                source_listing_id=zpid,
                source_record_id=zpid,
                display_address=f"{address}, {city}, {state} {postal}",
                city=city,
                state=state,
                postal_code=postal,
                latitude=prop.get("latitude"),
                longitude=prop.get("longitude"),
                property_type=property_type,
                bedrooms=prop.get("bedrooms"),
                bathrooms=prop.get("bathrooms"),
                living_area_sqft=prop.get("livingArea"),
                intent=ListingIntent.for_sale,
                status=status,
                list_price_cents=list_price_cents,
                photos=photos,
                raw_payload=prop,
                observed_at=datetime.now(UTC),
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("rapidapi_zillow.parse_failed", error=str(e))
            return None
