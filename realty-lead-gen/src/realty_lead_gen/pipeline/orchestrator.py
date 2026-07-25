"""End-to-end pipeline orchestrator.

Called by the arq worker for each ingest cycle. Steps:

    1. For each active saved-search region, ask each available source
       adapter to yield up to `limit` RawListings.
    2. Normalize each to a canonical form (address hash).
    3. Dedup against existing Property rows; upsert Property + append
       PropertySnapshot + upsert Listing + Photo rows.
    4. Enqueue enrichment jobs per property (photo grader, AVM, comps,
       signal detection).
    5. On enrichment completion, materialize a DealAnalysis + score
       per active persona.
    6. Materialize Lead rows per (property, user, persona) triple that
       clears the saved-search min_score.

Every step logs to `EnrichmentRun` for auditability.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from realty_lead_gen.logging import get_logger
from realty_lead_gen.models.buyer import SavedSearch
from realty_lead_gen.models.listing import Listing
from realty_lead_gen.models.photo import Photo
from realty_lead_gen.models.property import (
    Property,
    PropertySnapshot,
)
from realty_lead_gen.pipeline.dedup import find_existing_property_id
from realty_lead_gen.pipeline.normalize import normalize_raw_listing
from realty_lead_gen.sources.base import SearchRegion
from realty_lead_gen.utils.addr import normalize_address
from realty_lead_gen.utils.geo import point
from realty_lead_gen.utils.hashing import sha256_hex

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from realty_lead_gen.schemas.listing import RawListing
    from realty_lead_gen.sources import SourceAdapter

logger = get_logger(__name__)


@dataclass(slots=True)
class IngestReport:
    listings_seen: int = 0
    properties_created: int = 0
    properties_updated: int = 0
    listings_upserted: int = 0
    photos_added: int = 0
    errors: list[str] = field(default_factory=list)
    #: Every property id this run created or materially changed. The caller
    #: fans enrichment out over exactly these — enriching an untouched
    #: property burns LLM budget for no new information.
    touched_property_ids: set[uuid.UUID] = field(default_factory=set)
    #: Subset of the above that is brand new. New properties are enriched
    #: unconditionally; changed ones only when the change is material.
    new_property_ids: set[uuid.UUID] = field(default_factory=set)


class Orchestrator:
    def __init__(self, session: AsyncSession, adapters: list[SourceAdapter]) -> None:
        self._session = session
        self._adapters = adapters

    async def ingest_region(
        self,
        region: SearchRegion,
        limit_per_adapter: int = 200,
    ) -> IngestReport:
        report = IngestReport()
        for adapter in self._adapters:
            if not adapter.available:
                continue
            try:
                async for raw in adapter.fetch(region, limit_per_adapter):
                    report.listings_seen += 1
                    await self._process_one(raw, report)
            except Exception as e:
                logger.exception("orchestrator.adapter_failed", adapter=adapter.name)
                report.errors.append(f"{adapter.name}: {e}")
        return report

    async def _process_one(self, raw: RawListing, report: IngestReport) -> None:
        ingest = normalize_raw_listing(raw)
        existing_id = await find_existing_property_id(self._session, ingest.address_hash)

        if existing_id is None:
            prop = self._new_property(raw, ingest.address_hash)
            self._session.add(prop)
            await self._session.flush()
            report.properties_created += 1
            property_id = prop.id
            report.new_property_ids.add(property_id)
            report.touched_property_ids.add(property_id)
        else:
            property_id = existing_id
            report.properties_updated += 1

        snap = PropertySnapshot(
            property_id=property_id,
            source=raw.source,
            source_record_id=raw.source_record_id or raw.source_listing_id,
            observed_at=raw.observed_at,
            status=raw.status.value,
            list_price_cents=raw.list_price_cents,
            raw_payload=raw.raw_payload,
        )
        self._session.add(snap)

        # Upsert listing (source, source_listing_id) is unique
        listing_stmt = select(Listing).where(
            Listing.source == raw.source,
            Listing.source_listing_id == raw.source_listing_id,
        )
        listing = (await self._session.execute(listing_stmt)).scalar_one_or_none()
        now = datetime.now(UTC)
        if listing is None:
            listing = Listing(
                property_id=property_id,
                source=raw.source,
                source_listing_id=raw.source_listing_id,
                mls_number=raw.mls_number,
                intent=raw.intent,
                status=raw.status,
                list_price_cents=raw.list_price_cents,
                original_list_price_cents=raw.original_list_price_cents,
                list_date=raw.list_date,
                close_date=raw.close_date,
                close_price_cents=raw.close_price_cents,
                listing_agent_name=raw.listing_agent_name,
                listing_agent_phone=raw.listing_agent_phone,
                listing_agent_email=raw.listing_agent_email,
                listing_office_name=raw.listing_office_name,
                remarks_public=raw.remarks_public,
                remarks_private=raw.remarks_private,
                photos_url_manifest=[str(p.url) for p in raw.photos],
                raw_payload=raw.raw_payload,
                first_seen_at=now,
                last_seen_at=now,
            )
            self._session.add(listing)
            report.listings_upserted += 1
            report.touched_property_ids.add(property_id)
        else:
            # Only a price or status move is worth re-running paid enrichment
            # over. A listing re-appearing unchanged in a nightly sweep is the
            # common case and must not cost anything.
            price_moved = listing.list_price_cents != raw.list_price_cents
            status_moved = listing.status != raw.status
            listing.status = raw.status
            listing.list_price_cents = raw.list_price_cents
            listing.last_seen_at = now
            if price_moved or status_moved:
                report.touched_property_ids.add(property_id)
                logger.info(
                    "orchestrator.listing_changed",
                    property_id=str(property_id),
                    price_moved=price_moved,
                    status_moved=status_moved,
                )

        await self._insert_new_photos(property_id, raw, report)

    async def _insert_new_photos(
        self,
        property_id: uuid.UUID,
        raw: RawListing,
        report: IngestReport,
    ) -> None:
        """Insert photos this property has never seen before.

        Dedup is by sha256(url) scoped to the property. The same check is
        enforced by ``uq_photo__property_id__url_sha256`` so a concurrent
        ingest of the same listing cannot slip a duplicate past us — this
        query is the fast path, the constraint is the guarantee.
        """
        if not raw.photos:
            return

        incoming: dict[str, tuple[str, int, str | None]] = {}
        for p in raw.photos:
            url_str = str(p.url)
            incoming[sha256_hex(url_str)] = (url_str, p.order_index, p.caption)

        existing_hashes = set(
            (
                await self._session.execute(
                    select(Photo.url_sha256).where(
                        Photo.property_id == property_id,
                        Photo.url_sha256.in_(list(incoming)),
                    )
                )
            )
            .scalars()
            .all()
        )

        for digest, (url_str, order_index, caption) in incoming.items():
            if digest in existing_hashes:
                continue
            self._session.add(
                Photo(
                    property_id=property_id,
                    url=url_str,
                    url_sha256=digest,
                    order_index=order_index,
                    caption=caption,
                )
            )
            report.photos_added += 1

    def _new_property(self, raw: RawListing, address_hash: str) -> Property:
        norm = normalize_address(raw.display_address)
        return Property(
            address_hash=address_hash,
            street_number=raw.street_number or norm.street_number,
            street_name=raw.street_name or norm.street_name,
            unit=raw.unit or norm.unit,
            city=raw.city or norm.city,
            state=(raw.state or norm.state).upper(),
            postal_code=raw.postal_code or norm.postal_code,
            county_fips=raw.county_fips,
            apn=raw.apn,
            location=point(raw.latitude, raw.longitude),
            property_type=raw.property_type,
            year_built=raw.year_built,
            lot_size_sqft=raw.lot_size_sqft,
            living_area_sqft=raw.living_area_sqft,
            bedrooms=raw.bedrooms,
            bathrooms=raw.bathrooms,
            attributes={},
        )


async def load_active_saved_search_regions(
    session: AsyncSession,
) -> list[SearchRegion]:
    stmt = select(SavedSearch).where(SavedSearch.is_active)
    result = await session.execute(stmt)
    return [
        SearchRegion(
            postal_codes=tuple(ss.postal_codes or ()),
            cities=tuple(ss.cities or ()),
            regions=tuple(ss.regions or ()),
        )
        for ss in result.scalars().all()
    ]
