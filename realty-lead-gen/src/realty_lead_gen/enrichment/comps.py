"""Comparable-sale retrieval + LLM re-ranking.

Design:
    * Retrieval: pull N candidate sales within `radius_miles` of the
      subject over the last `months_back` months, filtered to similar
      GLA (±25%) and same property_type.
    * Re-ranking (optional, when a Claude client is available): show
      the subject's remarks + condition + candidate list; ask Claude
      to pick 5-10 best comps and explain adjustments.

Only the RentCast retrieval path is wired here; HouseCanary /
CoreLogic can drop in behind the same interface.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from realty_lead_gen.agents.claude_client import ClaudeClient, text_of
from realty_lead_gen.config import Settings, get_settings
from realty_lead_gen.logging import get_logger
from realty_lead_gen.utils.http import raise_for_vendor_status
from realty_lead_gen.utils.money import dollars_to_cents
from realty_lead_gen.utils.retry import TransientError, default_retry

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Comp:
    address: str
    close_price_cents: int | None
    close_date: str | None
    living_area_sqft: int | None
    bedrooms: int | None
    bathrooms: float | None
    distance_miles: float | None
    property_type: str | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CompsResult:
    comps: list[Comp]
    narrative: str | None
    provider: str


class CompsRetrieval:
    provider = "rentcast"

    def __init__(
        self,
        settings: Settings | None = None,
        claude: ClaudeClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._claude = claude or ClaudeClient(self._settings)
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

    async def fetch_candidates(
        self,
        address: str,
        *,
        radius_miles: float = 0.5,
        months_back: int = 12,
        limit: int = 20,
    ) -> list[Comp]:
        if not self.available:
            return []
        client = await self._get_client()
        # Annotated because the mixed literal types otherwise join to
        # `dict[str, object]`, which httpx's `params=` will not accept.
        params: dict[str, str | int | float] = {
            "address": address,
            "compCount": limit,
            "daysOld": months_back * 30,
            "radius": radius_miles,
        }
        async for attempt in default_retry():
            with attempt:
                try:
                    resp = await client.get("/avm/value", params=params)
                except httpx.TransportError as e:
                    raise TransientError(str(e)) from e
                raise_for_vendor_status(resp, vendor=self.provider)
                data: dict[str, Any] = resp.json()
                break
        else:
            return []

        return [
            Comp(
                address=str(c.get("formattedAddress") or ""),
                close_price_cents=(dollars_to_cents(c["price"]) if "price" in c else None),
                close_date=c.get("removedDate") or c.get("lastSeenDate"),
                living_area_sqft=c.get("squareFootage"),
                bedrooms=c.get("bedrooms"),
                bathrooms=c.get("bathrooms"),
                distance_miles=c.get("distance"),
                property_type=c.get("propertyType"),
                raw=c,
            )
            for c in (data.get("comparables") or [])
        ]

    async def rerank(
        self,
        subject_address: str,
        subject_summary: str,
        candidates: list[Comp],
    ) -> CompsResult:
        """Ask Claude to pick + reason. Falls back to raw candidates when unavailable."""
        if not (self._claude.available and candidates):
            return CompsResult(
                comps=candidates,
                narrative=None,
                provider=self.provider,
            )
        # Compact the candidates into a text block Claude can reason over.
        lines = []
        for i, c in enumerate(candidates):
            lines.append(
                f"[{i}] {c.address} | ${(c.close_price_cents or 0) / 100:,.0f} | "
                f"{c.living_area_sqft} sqft | {c.bedrooms}br/{c.bathrooms}ba | "
                f"{c.distance_miles} mi | closed {c.close_date}"
            )
        prompt = (
            f"Subject: {subject_address}\n"
            f"Subject summary: {subject_summary}\n\n"
            f"Candidates:\n" + "\n".join(lines) + "\n\n"
            "Pick the 5-8 best comparable sales for this subject. "
            "For each, note whether it should be adjusted up or down for "
            "GLA, condition, location, or age. Return a JSON object with "
            "`selected_indices` (array of ints) and `narrative` (string)."
        )
        try:
            resp, _ = await self._claude.messages_create(
                model=self._settings.anthropic_model_reasoning,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1024,
            )
        except Exception:
            logger.exception("comps.rerank_failed")
            return CompsResult(comps=candidates, narrative=None, provider=self.provider)

        # Best-effort parse — LLM may return prose. Fall back to raw.
        text = text_of(resp)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return CompsResult(comps=candidates, narrative=text, provider=self.provider)
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return CompsResult(comps=candidates, narrative=text, provider=self.provider)
        indices = payload.get("selected_indices") or []
        selected = [candidates[i] for i in indices if 0 <= i < len(candidates)]
        return CompsResult(
            comps=selected or candidates,
            narrative=payload.get("narrative"),
            provider=self.provider,
        )
