"""Capabilities 1-4: Property Intelligence, Financial Analysis,
Location & Infrastructure Analysis, Risk Assessment.

Plain functions -- no protocol/envelope knowledge here. Bad input raises
ValueError; a missing key or an unreachable/failing dependency raises
whatever clients.py raised (RuntimeError / ConnectionError / TimeoutError)
straight through, uncaught, for agent_main.py to turn into an envelope.
"""

from __future__ import annotations

import hashlib
import re
import statistics
from typing import Any

from budget import DeadlineBudget
from clients import geocode, tavily_search

# ---------------------------------------------------------------------------
# Capability 1 -- Property Intelligence
# ---------------------------------------------------------------------------

RESIDENTIAL_HINTS = ("apartment", "flat", "villa", "house", "plot", "bhk", "residential")
COMMERCIAL_HINTS = ("commercial", "office space", "shop", "retail", "warehouse", "showroom")


def build_property_profile(
    *,
    property_url: str | None,
    address: str | None,
    asking_price_inr: float | None,
    area_sqft: float | None,
    budget: DeadlineBudget | None = None,
) -> dict[str, Any]:
    if not property_url and not address:
        raise ValueError("either 'property_url' or 'address' is required to identify a property")

    lowered = f"{property_url or ''} {address or ''}".lower()
    if any(hint in lowered for hint in COMMERCIAL_HINTS) and not any(hint in lowered for hint in RESIDENTIAL_HINTS):
        raise ValueError("this agent evaluates residential property only")

    query = address or property_url
    results: list[dict[str, Any]] = []
    if query:
        try:
            # Best-effort corroboration only -- a missing key or a search
            # miss doesn't make the property unidentifiable, so fall back
            # to what the caller told us instead of failing the request.
            timeout = budget.for_call(1) if budget is not None else 12.0
            results = tavily_search(f"{query} builder property details", timeout=timeout)
        except Exception:
            results = []

    builder = _extract_builder(address or "", results)
    configuration = _extract_configuration(lowered)
    property_type = _infer_property_type(lowered)
    location = _extract_location(address, results)

    if not location:
        raise ValueError("could not determine a location for this property; provide 'address'")

    # Same exclusiveMinimum: 0 rule as financial_analysis / agent.yaml — a
    # negative asking price used to slip through and yield a negative
    # price_per_sqft because this path only checked truthiness.
    price_per_sqft = _resolve_price_per_sqft(
        price_per_sqft=None, asking_price_inr=asking_price_inr, area_sqft=area_sqft
    )

    property_id = "prop_" + hashlib.sha1((property_url or address or "").encode("utf-8")).hexdigest()[:10]

    return {
        "property_id": property_id,
        "builder": builder,
        "property_type": property_type,
        "configuration": configuration,
        "location": location,
        "price_per_sqft": price_per_sqft,
    }


def _infer_property_type(lowered: str) -> str:
    if "villa" in lowered:
        return "Villa"
    if "plot" in lowered:
        return "Plot"
    return "Apartment"


def _extract_builder(address: str, results: list[dict[str, Any]]) -> str | None:
    # In Indian listings the builder/project name is often the first
    # comma-separated segment of the address, e.g. "Prestige Lakeside
    # Habitat, Whitefield, Bangalore".
    first_segment = address.split(",", maxsplit=1)[0].strip() if address else ""
    if first_segment and len(first_segment.split()) <= 6:
        return first_segment
    for result in results:
        title = result.get("title", "") if isinstance(result, dict) else ""
        match = re.search(r"by ([A-Z][\w&. ]{2,40}?)(?:\s*[-|,]|$)", title)
        if match:
            return match.group(1).strip()
    return None


def _extract_configuration(lowered: str) -> str | None:
    match = re.search(r"(\d)\s*bhk", lowered)
    return f"{match.group(1)}BHK" if match else None


def _extract_location(address: str | None, results: list[dict[str, Any]]) -> str | None:
    if address:
        parts = [p.strip() for p in address.split(",") if p.strip()]
        if len(parts) >= 2:
            return ", ".join(parts[-2:])
        if parts:
            return parts[-1]
    for result in results:
        title = result.get("title", "") if isinstance(result, dict) else ""
        if title:
            return title
    return None


# ---------------------------------------------------------------------------
# Capability 2 -- Financial Analysis
# ---------------------------------------------------------------------------

DEFAULT_RENTAL_YIELD_PERCENT = 3.0
ASSUMED_ANNUAL_APPRECIATION_PERCENT = 8.0


def analyze_financials(
    *,
    location: str,
    price_per_sqft: float | None = None,
    asking_price_inr: float | None = None,
    area_sqft: float | None = None,
    budget: DeadlineBudget | None = None,
) -> dict[str, Any]:
    resolved_price_per_sqft = _resolve_price_per_sqft(
        price_per_sqft=price_per_sqft, asking_price_inr=asking_price_inr, area_sqft=area_sqft
    )

    timeout_a = budget.for_call(2) if budget is not None else 12.0
    comparable_results = tavily_search(
        f"{location} property price per sqft average", timeout=timeout_a
    )
    comparable_prices = _extract_prices_per_sqft(comparable_results)
    market_avg = statistics.median(comparable_prices) if comparable_prices else resolved_price_per_sqft

    if resolved_price_per_sqft is not None and market_avg:
        premium_percent = round(((resolved_price_per_sqft - market_avg) / market_avg) * 100, 1)
        market_value = _classify_market_value(premium_percent)
    else:
        # No price data supplied at all -- yield/ROI can still be
        # estimated from the location alone, but market position can't.
        premium_percent = None
        market_value = None

    timeout_b = budget.for_call(1) if budget is not None else 12.0
    rental_results = tavily_search(
        f"{location} monthly rental yield apartment", timeout=timeout_b
    )
    rental_yield = _extract_rental_yield(rental_results) or DEFAULT_RENTAL_YIELD_PERCENT

    price_penalty = max(premium_percent, 0) if premium_percent is not None else 0
    roi = round(rental_yield + ASSUMED_ANNUAL_APPRECIATION_PERCENT - price_penalty * 0.2, 1)
    financial_score = _score_financials(premium_percent, rental_yield, roi)

    return {
        "market_value": market_value,
        "premium_percent": premium_percent,
        "estimated_rental_yield_percent": round(rental_yield, 1),
        "estimated_roi_percent": roi,
        "financial_score": financial_score,
    }


def _resolve_price_per_sqft(
    *, price_per_sqft: float | None, asking_price_inr: float | None, area_sqft: float | None
) -> float | None:
    for name, value in (
        ("price_per_sqft", price_per_sqft),
        ("asking_price_inr", asking_price_inr),
        ("area_sqft", area_sqft),
    ):
        if value is not None and value <= 0:
            raise ValueError(f"'{name}' must be positive")

    if price_per_sqft is not None:
        return price_per_sqft
    if asking_price_inr is not None and area_sqft is not None:
        return round(asking_price_inr / area_sqft, 2)
    return None


def _classify_market_value(premium_percent: float) -> str:
    if premium_percent <= -5:
        return "Undervalued"
    if premium_percent <= 8:
        return "Fair"
    return "Overvalued"


def _extract_prices_per_sqft(results: list[dict[str, Any]]) -> list[float]:
    prices: list[float] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        text = f"{result.get('title', '')} {result.get('content', '')}"
        for match in re.finditer(r"(?:rs\.?|inr|₹)\s?([\d,]{3,7})\s*(?:/|per)\s*sq", text, re.IGNORECASE):
            try:
                prices.append(float(match.group(1).replace(",", "")))
            except ValueError:
                continue
    return prices


def _extract_rental_yield(results: list[dict[str, Any]]) -> float | None:
    for result in results:
        if not isinstance(result, dict):
            continue
        text = f"{result.get('title', '')} {result.get('content', '')}"
        match = re.search(r"([\d.]{1,4})\s*%\s*(?:rental )?yield", text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


def _score_financials(premium_percent: float | None, rental_yield: float, roi: float) -> float:
    score = 5.0
    if premium_percent is not None:
        score -= max(premium_percent, 0) * 0.15
        score += max(-premium_percent, 0) * 0.1
    score += (rental_yield - DEFAULT_RENTAL_YIELD_PERCENT) * 0.5
    score += (roi - 10) * 0.1
    return round(min(max(score, 0.0), 10.0), 1)


# ---------------------------------------------------------------------------
# Capability 3 -- Location & Infrastructure Analysis
# ---------------------------------------------------------------------------

GROWTH_KEYWORDS_HIGH = ("metro", "it corridor", "sez", "ring road", "expressway", "airport")
GROWTH_KEYWORDS_MEDIUM = ("upcoming", "planned", "proposed")
CONNECTIVITY_KEYWORDS = ("metro", "highway", "airport", "railway", "bus")
AMENITY_KEYWORDS = ("school", "hospital", "mall", "market", "restaurant")


def analyze_location(*, location: str, budget: DeadlineBudget | None = None) -> dict[str, Any]:
    if not location or not location.strip():
        raise ValueError("'location' must be a non-empty string")

    # Best-effort corroboration that the locality is a real, resolvable
    # place. No key required (OpenStreetMap/Nominatim), and a miss never
    # fails the capability -- it just means coordinates come back null.
    # Three outbound calls share the deadline: geocode + two Tavily searches.
    geo_timeout = budget.for_call(3) if budget is not None else 8.0
    coordinates = geocode(location, timeout=geo_timeout)

    infra_timeout = budget.for_call(2) if budget is not None else 12.0
    infra_results = tavily_search(
        f"{location} upcoming infrastructure metro road development", timeout=infra_timeout
    )
    amenity_timeout = budget.for_call(1) if budget is not None else 12.0
    amenity_results = tavily_search(
        f"{location} connectivity schools hospitals malls", timeout=amenity_timeout
    )

    planned_infrastructure = _extract_infrastructure(infra_results)
    connectivity_score = _keyword_score(amenity_results, CONNECTIVITY_KEYWORDS)
    amenities_score = _keyword_score(amenity_results, AMENITY_KEYWORDS)
    growth_potential = _classify_growth(infra_results)

    bonus = {"High": 1.0, "Medium": 0.3, "Low": 0.0}[growth_potential]
    location_score = round(min((connectivity_score + amenities_score) / 2 + bonus, 10.0), 1)

    return {
        "connectivity_score": connectivity_score,
        "amenities_score": amenities_score,
        "growth_potential": growth_potential,
        "planned_infrastructure": planned_infrastructure,
        "location_score": location_score,
        "coordinates": coordinates,
    }


def _classify_growth(results: list[dict[str, Any]]) -> str:
    text = _joined_text(results).lower()
    if any(k in text for k in GROWTH_KEYWORDS_HIGH):
        return "High"
    if any(k in text for k in GROWTH_KEYWORDS_MEDIUM):
        return "Medium"
    return "Low"


def _keyword_score(results: list[dict[str, Any]], keywords: tuple[str, ...]) -> float:
    text = _joined_text(results).lower()
    hits = sum(1 for k in keywords if k in text)
    return round(min(5.0 + hits * 1.0, 10.0), 1)


def _extract_infrastructure(results: list[dict[str, Any]]) -> list[str]:
    found: set[str] = set()
    for result in results:
        if not isinstance(result, dict):
            continue
        text = f"{result.get('title', '')} {result.get('content', '')}"
        for match in re.finditer(
            r"([A-Z][\w ]{2,30}(?:Metro|Expressway|Ring Road|Corridor|Line|Phase\s?\d*))", text
        ):
            found.add(match.group(1).strip())
    return sorted(found)[:5]


def _joined_text(results: list[dict[str, Any]]) -> str:
    return " ".join(
        f"{r.get('title', '')} {r.get('content', '')}" for r in results if isinstance(r, dict)
    )


# ---------------------------------------------------------------------------
# Capability 4 -- Risk Assessment
# ---------------------------------------------------------------------------

NEGATIVE_BUILDER_KEYWORDS = ("delay", "delayed", "fraud", "litigation", "case", "dispute", "cheat", "complaint", "scam")
FLOOD_KEYWORDS = ("flood", "waterlog", "waterlogging", "low-lying")
MARKET_SLOWDOWN_KEYWORDS = ("slowdown", "oversupply", "decline", "stagnant")
RISK_RANK = {"Low": 0, "Medium": 1, "High": 2}


def assess_risk(
    *,
    builder: str | None = None,
    location: str,
    property_type: str | None = None,
    budget: DeadlineBudget | None = None,
) -> dict[str, Any]:
    if not location or not location.strip():
        raise ValueError("'location' is required")

    identified_risks: list[str] = []
    if not property_type:
        identified_risks.append("property type not supplied; type-specific risk factors were not checked")

    # Three Tavily searches share the deadline (builder + environment + market).
    if builder:
        builder_results = tavily_search(
            f"{builder} builder complaints litigation reviews",
            timeout=budget.for_call(3) if budget is not None else 12.0,
        )
    else:
        # Still confirms the key is configured even with no builder to
        # search for, so this fails `unavailable` (not a false "Low
        # risk") when TAVILY_API_KEY is missing.
        tavily_search(
            f"{location} real estate builder track record",
            timeout=budget.for_call(3) if budget is not None else 12.0,
        )
        builder_results = []

    builder_hits = _count_keyword_hits(builder_results, NEGATIVE_BUILDER_KEYWORDS)
    if not builder:
        builder_legal_risk = "Medium"
        identified_risks.append("builder could not be identified; legal/track-record risk could not be verified")
    else:
        builder_legal_risk = "High" if builder_hits >= 3 else "Medium" if builder_hits >= 1 else "Low"
        if builder_hits:
            identified_risks.append(f"{builder_hits} negative signal(s) found for the builder in public search results")

    env_results = tavily_search(
        f"{location} flooding environmental risk",
        timeout=budget.for_call(2) if budget is not None else 12.0,
    )
    flood_hits = _count_keyword_hits(env_results, FLOOD_KEYWORDS)
    environmental_risk = "High" if flood_hits >= 2 else "Medium" if flood_hits >= 1 else "Low"
    if flood_hits:
        identified_risks.append("locality has reported flooding or waterlogging history")

    market_results = tavily_search(
        f"{location} real estate market slowdown oversupply",
        timeout=budget.for_call(1) if budget is not None else 12.0,
    )
    market_hits = _count_keyword_hits(market_results, MARKET_SLOWDOWN_KEYWORDS)
    market_risk = "High" if market_hits >= 2 else "Medium" if market_hits >= 1 else "Low"
    if market_hits:
        identified_risks.append("locality market shows signs of slowdown or oversupply in recent coverage")

    overall_risk = _combine_risk_levels([builder_legal_risk, environmental_risk, market_risk])

    return {
        "builder_legal_risk": builder_legal_risk,
        "environmental_risk": environmental_risk,
        "market_risk": market_risk,
        "overall_risk": overall_risk,
        "identified_risks": identified_risks,
    }


def _count_keyword_hits(results: list[dict[str, Any]], keywords: tuple[str, ...]) -> int:
    text = _joined_text(results).lower()
    return sum(1 for k in keywords if k in text)


def _combine_risk_levels(levels: list[str]) -> str:
    highs = sum(1 for lvl in levels if lvl == "High")
    if highs >= 2:
        return "High"
    return max(levels, key=lambda lvl: RISK_RANK[lvl])
