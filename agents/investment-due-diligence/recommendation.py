"""Capability 5 -- Investment Recommendation.

The only capability that calls an LLM: synthesizes whatever evidence the
caller has -- raw outputs from the other capabilities, or just their
convenience-shortcut scores, or a mix of both -- into a final
BUY / NEGOTIATE / REJECT call via Groq. Nothing is strictly required; a
caller who only ran risk_assessment still gets a recommendation, just a
less confident one, because the model is told exactly what evidence is
and isn't present.
"""

from __future__ import annotations

import json
from typing import Any

from clients import groq_chat_json

SYSTEM_PROMPT = (
    "You are a conservative residential real-estate investment analyst in India. "
    "You will be given an 'evidence' object built from whatever the caller had "
    "available -- it may be partial. Missing evidence is not an error; reason with "
    "what's there, and lower your confidence_percent in proportion to what's "
    "missing rather than guessing. Respond with a single JSON object with exactly "
    "these keys: "
    '"recommendation" (one of "BUY", "NEGOTIATE", "REJECT"), '
    '"confidence_percent" (integer 0-100), '
    '"key_strengths" (array of short strings), '
    '"key_concerns" (array of short strings, and include any evidence gaps here), '
    '"recommended_offer_price_inr" (number or null, only if an asking price was given). '
    "Return only the JSON object, nothing else."
)

VALID_RISK_LEVELS = ("Low", "Medium", "High")


def recommend(
    *,
    property_profile: dict[str, Any] | None = None,
    financial_analysis: dict[str, Any] | None = None,
    location_analysis: dict[str, Any] | None = None,
    risk_assessment: dict[str, Any] | None = None,
    financial_score: float | None = None,
    location_score: float | None = None,
    overall_risk: str | None = None,
    asking_price_inr: float | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    evidence = _build_evidence(
        property_profile=property_profile,
        financial_analysis=financial_analysis,
        location_analysis=location_analysis,
        risk_assessment=risk_assessment,
        financial_score=financial_score,
        location_score=location_score,
        overall_risk=overall_risk,
        asking_price_inr=asking_price_inr,
    )
    if not evidence:
        raise ValueError(
            "at least one of property_profile, financial_analysis, location_analysis, "
            "risk_assessment, financial_score, location_score, or overall_risk is required"
        )

    parsed, usage = groq_chat_json(json.dumps({"evidence": evidence}), system=SYSTEM_PROMPT)
    return _validate_output(parsed), usage


def _build_evidence(
    *,
    property_profile: dict[str, Any] | None,
    financial_analysis: dict[str, Any] | None,
    location_analysis: dict[str, Any] | None,
    risk_assessment: dict[str, Any] | None,
    financial_score: float | None,
    location_score: float | None,
    overall_risk: str | None,
    asking_price_inr: float | None,
) -> dict[str, Any]:
    """Merges the raw sub-capability outputs with the shortcut fields.

    An explicit shortcut (e.g. `financial_score`) wins over the same
    value nested in a raw sub-object, since the caller supplied it
    directly and more recently than whatever produced the sub-object.
    """
    evidence: dict[str, Any] = {}

    if property_profile:
        evidence["property_profile"] = property_profile

    if financial_analysis:
        evidence["financial_analysis"] = financial_analysis
    resolved_financial_score = financial_score
    if resolved_financial_score is None and financial_analysis:
        resolved_financial_score = financial_analysis.get("financial_score")
    if resolved_financial_score is not None:
        evidence["financial_score"] = resolved_financial_score

    if location_analysis:
        evidence["location_analysis"] = location_analysis
    resolved_location_score = location_score
    if resolved_location_score is None and location_analysis:
        resolved_location_score = location_analysis.get("location_score")
    if resolved_location_score is not None:
        evidence["location_score"] = resolved_location_score

    if risk_assessment:
        evidence["risk_assessment"] = risk_assessment
    resolved_overall_risk = overall_risk
    if resolved_overall_risk is None and risk_assessment:
        resolved_overall_risk = risk_assessment.get("overall_risk")
    if resolved_overall_risk is not None:
        if resolved_overall_risk not in VALID_RISK_LEVELS:
            raise ValueError(f"'overall_risk' must be one of {', '.join(VALID_RISK_LEVELS)}")
        evidence["overall_risk"] = resolved_overall_risk

    if asking_price_inr is not None:
        evidence["asking_price_inr"] = asking_price_inr

    return evidence


def _validate_output(parsed: dict[str, Any]) -> dict[str, Any]:
    recommendation = parsed.get("recommendation")
    if recommendation not in ("BUY", "NEGOTIATE", "REJECT"):
        recommendation = "NEGOTIATE"

    try:
        confidence = max(0, min(100, int(parsed.get("confidence_percent", 50))))
    except (TypeError, ValueError):
        confidence = 50

    offer_price = parsed.get("recommended_offer_price_inr")
    if isinstance(offer_price, bool) or not isinstance(offer_price, (int, float)):
        offer_price = None

    return {
        "recommendation": recommendation,
        "confidence_percent": confidence,
        "key_strengths": _str_list(parsed.get("key_strengths")),
        "key_concerns": _str_list(parsed.get("key_concerns")),
        "recommended_offer_price_inr": offer_price,
    }


def _str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, (str, int, float))]
    return []