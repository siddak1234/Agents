"""AVM adapter — RentCast reference implementation.

RentCast covers AVM + rental estimate in one API and is priced for
small-SaaS scale ($99-$449/mo). Alternatives (HouseCanary, ATTOM,
CoreLogic) can be dropped in behind the same interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import httpx

from realty_lead_gen.config import Settings, get_settings
from realty_lead_gen.logging import get_logger
from realty_lead_gen.utils.http import raise_for_vendor_status
from realty_lead_gen.utils.money import dollars_to_cents
from realty_lead_gen.utils.retry import PermanentError, TransientError, default_retry

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ValuationResult:
    avm_value_cents: int | None
    avm_low_cents: int | None
    avm_high_cents: int | None
    avm_confidence: float | None
    provider: str
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RentEstimateResult:
    monthly_rent_cents: int | None
    rent_low_cents: int | None
    rent_high_cents: int | None
    provider: str
    raw: dict[str, Any]


class ValuationStep:
    """RentCast-backed valuation."""

    kind = "avm_valuation"
    provider = "rentcast"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: httpx.AsyncClient | None = None

    @property
    def available(self) -> bool:
        return self._settings.rentcast_api_key is not None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            key = self._settings.rentcast_api_key
            self._client = httpx.AsyncClient(
                base_url="https://api.rentcast.io/v1",
                timeout=httpx.Timeout(15.0, connect=5.0),
                headers={
                    "X-Api-Key": key.get_secret_value() if key else "",
                    "Accept": "application/json",
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
                # Checked ahead of the shared triage on purpose: RentCast
                # returns 404 for "no AVM exists for this address", which is
                # an ordinary answer for a rural or new-construction parcel,
                # not an error. `{}` is the caller's "no data" sentinel.
                if resp.status_code == HTTPStatus.NOT_FOUND:
                    return {}
                raise_for_vendor_status(resp, vendor=self.provider)
                data: dict[str, Any] = resp.json()
                return data
        raise RuntimeError("unreachable")

    async def value(self, address: str) -> ValuationResult | None:
        if not self.available:
            return None
        try:
            data = await self._request("/avm/value", {"address": address})
        except PermanentError as e:
            # TRY400 suppressed: PermanentError is self-describing; see its docstring.
            logger.error("rentcast.avm_failed", error=str(e), address=address)  # noqa: TRY400
            return None
        if not data:
            return None
        return ValuationResult(
            avm_value_cents=dollars_to_cents(data["price"]) if "price" in data else None,
            avm_low_cents=dollars_to_cents(data["priceRangeLow"])
            if "priceRangeLow" in data
            else None,
            avm_high_cents=dollars_to_cents(data["priceRangeHigh"])
            if "priceRangeHigh" in data
            else None,
            avm_confidence=None,  # RentCast doesn't publish per-call confidence
            provider=self.provider,
            raw=data,
        )

    async def rent(self, address: str) -> RentEstimateResult | None:
        if not self.available:
            return None
        try:
            data = await self._request("/avm/rent/long-term", {"address": address})
        except PermanentError as e:
            logger.error("rentcast.rent_failed", error=str(e), address=address)  # noqa: TRY400
            return None
        if not data:
            return None
        return RentEstimateResult(
            monthly_rent_cents=dollars_to_cents(data["rent"]) if "rent" in data else None,
            rent_low_cents=dollars_to_cents(data["rentRangeLow"])
            if "rentRangeLow" in data
            else None,
            rent_high_cents=dollars_to_cents(data["rentRangeHigh"])
            if "rentRangeHigh" in data
            else None,
            provider=self.provider,
            raw=data,
        )
