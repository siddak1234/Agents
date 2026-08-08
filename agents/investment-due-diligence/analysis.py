"""Capabilities 1-4: Property Intelligence, Financial Analysis,
Location & Infrastructure Analysis, Risk Assessment.

Each is one Claude call: it researches the question with web search and
reports through a tool whose schema mirrors that capability's
`output_schema` in agent.yaml.

What stays deterministic here is everything that is *not* analysis —
validating the caller's input, deriving price per square foot, minting a
property id. Those have one right answer and belong in code. Judgment about
a locality's prospects does not, and asking a model to search for it beats
regexes over search snippets, which is what this file used to do.

Bad input raises ValueError; anything clients.py raises passes straight
through, uncaught, for agent_main.py to turn into an envelope.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from budget import DeadlineBudget
from clients import research

SYSTEM = (
    "You are a conservative residential real-estate analyst covering the Indian "
    "market. All prices are INR. Research the question with web search before "
    "answering — current listing rates, civic and infrastructure news, and "
    "builder track record are exactly the things your training data is stale "
    "on. Prefer recent, named sources over general impressions.\n\n"
    "Report only what your sources support. Where the evidence does not settle "
    "a field, return null for it rather than a plausible-looking guess; a "
    "confident wrong number is worse than an honest gap. Note the limits of "
    "what you found rather than filling them in. Then report by calling the "
    "tool you were given, exactly once."
)

RESIDENTIAL_HINTS = ("apartment", "flat", "villa", "house", "plot", "bhk", "residential")
COMMERCIAL_HINTS = ("commercial", "office space", "shop", "retail", "warehouse", "showroom")

#: Scores are 0-10 throughout, matching agent.yaml.
_SCORE = {"type": "number", "minimum": 0, "maximum": 10}


def _mentions(text: str, hints: tuple[str, ...]) -> bool:
    """Whole-word hint match against already-lowercased text.

    Substring matching rejected real homes: "shop" sits inside "Bishop"
    (Bishop Cotton Road in Bangalore, Bishop Lefroy Road in Kolkata) and
    inside "workshop", so a residential listing came back invalid_request as
    though it were a storefront.
    """
    return any(re.search(rf"\b{re.escape(hint)}\b", text) for hint in hints)


def _timeout(budget: DeadlineBudget | None, calls_left: int = 1) -> float:
    return budget.for_call(calls_left) if budget is not None else 90.0


# ---------------------------------------------------------------------------
# Capability 1 -- Property Intelligence
# ---------------------------------------------------------------------------

PROPERTY_TOOL: dict[str, Any] = {
    "name": "record_property_profile",
    "description": "Record the verified profile of this residential property.",
    "input_schema": {
        "type": "object",
        "properties": {
            "builder": {
                "type": ["string", "null"],
                "description": "Developer or project name, or null if not established.",
            },
            "property_type": {
                "type": "string",
                "description": "Apartment, Villa, Plot, or another concrete type.",
            },
            "configuration": {
                "type": ["string", "null"],
                "description": 'Unit configuration such as "3BHK", or null.',
            },
            "location": {
                "type": "string",
                "description": "Locality and city, as a caller would recognise it.",
            },
        },
        "required": ["property_type", "location"],
    },
}


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
    if _mentions(lowered, COMMERCIAL_HINTS) and not _mentions(lowered, RESIDENTIAL_HINTS):
        raise ValueError("this agent evaluates residential property only")

    # Same exclusiveMinimum: 0 rule as agent.yaml, checked before anything is
    # billed: a negative price is the caller's mistake, not a research task.
    price_per_sqft = _resolve_price_per_sqft(
        price_per_sqft=None, asking_price_inr=asking_price_inr, area_sqft=area_sqft
    )

    recorded = research(
        system=SYSTEM,
        prompt=(
            "Identify this residential property and report its profile.\n"
            f"Address: {address or '(not supplied)'}\n"
            f"Listing URL: {property_url or '(not supplied)'}\n"
            "Establish the developer or project name, the property type, the "
            "unit configuration, and the locality. Leave a field null if your "
            "sources do not establish it."
        ),
        tool=PROPERTY_TOOL,
        timeout=_timeout(budget),
    )

    return {
        # Minted here, not asked of the model: an id must be stable for the
        # same input, and a model has no way to guarantee that.
        "property_id": "prop_"
        + hashlib.sha1((property_url or address or "").encode("utf-8")).hexdigest()[:10],
        "builder": recorded.get("builder"),
        "property_type": recorded.get("property_type"),
        "configuration": recorded.get("configuration"),
        "location": recorded.get("location"),
        "price_per_sqft": price_per_sqft,
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


# ---------------------------------------------------------------------------
# Capability 2 -- Financial Analysis
# ---------------------------------------------------------------------------

FINANCIAL_TOOL: dict[str, Any] = {
    "name": "record_financial_analysis",
    "description": "Record the financial position of this property against its locality.",
    "input_schema": {
        "type": "object",
        "properties": {
            "market_value": {
                "type": ["string", "null"],
                "enum": ["Undervalued", "Fair", "Overvalued", None],
                "description": (
                    "Position against comparable rates. Null when no comparable "
                    "rate was found, or no price was supplied to compare."
                ),
            },
            "premium_percent": {
                "type": ["number", "null"],
                "description": (
                    "Percent above (positive) or below (negative) the comparable "
                    "rate. Null whenever market_value is null."
                ),
            },
            "estimated_rental_yield_percent": {
                "type": "number",
                "description": "Gross annual rental yield for the locality, in percent.",
            },
            "estimated_roi_percent": {
                "type": "number",
                "description": "Estimated annual return including expected appreciation.",
            },
            "financial_score": dict(_SCORE, description="Financial attractiveness, 0-10."),
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Short notes on what the figures rest on, and any gaps.",
            },
        },
        "required": ["estimated_rental_yield_percent", "estimated_roi_percent", "financial_score"],
    },
}


def analyze_financials(
    *,
    location: str,
    price_per_sqft: float | None = None,
    asking_price_inr: float | None = None,
    area_sqft: float | None = None,
    budget: DeadlineBudget | None = None,
) -> dict[str, Any]:
    resolved = _resolve_price_per_sqft(
        price_per_sqft=price_per_sqft, asking_price_inr=asking_price_inr, area_sqft=area_sqft
    )
    asking = (
        f"{resolved} INR per sqft" if resolved is not None else "(no asking price supplied)"
    )
    recorded = research(
        system=SYSTEM,
        prompt=(
            f"Assess the financial attractiveness of residential property in {location}.\n"
            f"This property's asking rate: {asking}\n"
            "Find current comparable per-square-foot rates for the locality and "
            "the prevailing gross rental yield. Report where this property sits "
            "against those comparables. If you cannot find a comparable rate, or "
            "no asking rate was supplied, return market_value and "
            "premium_percent as null rather than estimating them."
        ),
        tool=FINANCIAL_TOOL,
        timeout=_timeout(budget),
    )
    return {
        "market_value": recorded.get("market_value"),
        "premium_percent": recorded.get("premium_percent"),
        "estimated_rental_yield_percent": recorded.get("estimated_rental_yield_percent"),
        "estimated_roi_percent": recorded.get("estimated_roi_percent"),
        "financial_score": recorded.get("financial_score"),
        "evidence": recorded.get("evidence", []),
    }


# ---------------------------------------------------------------------------
# Capability 3 -- Location & Infrastructure Analysis
# ---------------------------------------------------------------------------

LOCATION_TOOL: dict[str, Any] = {
    "name": "record_location_analysis",
    "description": "Record connectivity, amenities and growth outlook for a locality.",
    "input_schema": {
        "type": "object",
        "properties": {
            "connectivity_score": dict(_SCORE, description="Transport connectivity, 0-10."),
            "amenities_score": dict(_SCORE, description="Schools, healthcare, retail, 0-10."),
            "growth_potential": {
                "type": "string",
                "enum": ["Low", "Medium", "High"],
                "description": "Long-term growth outlook.",
            },
            "planned_infrastructure": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Named projects announced or under construction.",
            },
            "location_score": dict(_SCORE, description="Overall locality quality, 0-10."),
            "coordinates": {
                "type": ["object", "null"],
                "properties": {
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                },
                "description": "Approximate centre of the locality, or null.",
            },
        },
        "required": [
            "connectivity_score",
            "amenities_score",
            "growth_potential",
            "location_score",
        ],
    },
}


def analyze_location(*, location: str, budget: DeadlineBudget | None = None) -> dict[str, Any]:
    if not location or not location.strip():
        raise ValueError("'location' must be a non-empty string")

    recorded = research(
        system=SYSTEM,
        prompt=(
            f"Assess {location} as a place to own a home.\n"
            "Cover transport connectivity, everyday amenities, and infrastructure "
            "that is announced or under construction. Name the specific projects "
            "you find and say whether each is proposed, approved or building — a "
            "project that has been announced for a decade is not growth."
        ),
        tool=LOCATION_TOOL,
        timeout=_timeout(budget),
    )
    return {
        "connectivity_score": recorded.get("connectivity_score"),
        "amenities_score": recorded.get("amenities_score"),
        "growth_potential": recorded.get("growth_potential"),
        "planned_infrastructure": recorded.get("planned_infrastructure", []),
        "location_score": recorded.get("location_score"),
        "coordinates": recorded.get("coordinates"),
    }


# ---------------------------------------------------------------------------
# Capability 4 -- Risk Assessment
# ---------------------------------------------------------------------------

RISK_LEVELS = ["Low", "Medium", "High"]

RISK_TOOL: dict[str, Any] = {
    "name": "record_risk_assessment",
    "description": "Record builder, environmental and market risk for this property.",
    "input_schema": {
        "type": "object",
        "properties": {
            "builder_legal_risk": {
                "type": "string",
                "enum": RISK_LEVELS,
                "description": "Delivery, litigation and track-record risk.",
            },
            "environmental_risk": {
                "type": "string",
                "enum": RISK_LEVELS,
                "description": "Flooding, waterlogging and similar locality risk.",
            },
            "market_risk": {
                "type": "string",
                "enum": RISK_LEVELS,
                "description": "Oversupply or slowdown risk in this locality.",
            },
            "overall_risk": {
                "type": "string",
                "enum": RISK_LEVELS,
                "description": "The level a buyer should act on.",
            },
            "identified_risks": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "One short line per concrete finding, and per thing you could "
                    "not verify. An absence of evidence is a limitation, not a "
                    "clean record — say which you found."
                ),
            },
        },
        "required": ["builder_legal_risk", "environmental_risk", "market_risk", "overall_risk"],
    },
}


def assess_risk(
    *,
    builder: str | None = None,
    location: str,
    property_type: str | None = None,
    budget: DeadlineBudget | None = None,
) -> dict[str, Any]:
    if not location or not location.strip():
        raise ValueError("'location' is required")

    recorded = research(
        system=SYSTEM,
        prompt=(
            f"Assess the risks of buying residential property in {location}.\n"
            f"Developer: {builder or '(not supplied)'}\n"
            f"Property type: {property_type or '(not supplied)'}\n"
            "Cover the developer's delivery and litigation record, the "
            "locality's flooding and environmental history, and whether the "
            "local market shows oversupply or slowdown. Read claims in context: "
            "coverage stating a locality has no flooding history is not a flood "
            "risk. Where you could not verify something, record that as a "
            "limitation rather than treating it as an all-clear."
        ),
        tool=RISK_TOOL,
        timeout=_timeout(budget),
    )
    return {
        "builder_legal_risk": recorded.get("builder_legal_risk"),
        "environmental_risk": recorded.get("environmental_risk"),
        "market_risk": recorded.get("market_risk"),
        "overall_risk": recorded.get("overall_risk"),
        "identified_risks": recorded.get("identified_risks", []),
    }
