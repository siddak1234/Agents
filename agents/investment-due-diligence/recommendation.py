"""Capability 5 -- Investment Recommendation.

Synthesizes whatever evidence the caller has -- raw outputs from the other
capabilities, or just their convenience-shortcut scores, or a mix of both --
into a final BUY / NEGOTIATE / REJECT call. Unlike the other capabilities it
reasons over evidence it was handed rather than researching afresh, though
it may still search to check a claim. Nothing is strictly required; a
caller who only ran risk_assessment still gets a recommendation, just a
less confident one, because the model is told exactly what evidence is
and isn't present.

Wire field names match the capabilities that produce them
(property_intelligence, financial_analysis,
location_infrastructure_analysis, risk_assessment).
"""

from __future__ import annotations

import json
from typing import Any

from budget import DeadlineBudget
from clients import research

RECOMMENDATIONS = ("BUY", "NEGOTIATE", "REJECT")

#: Named once. It is the tool definition, the forced `tool_choice` target,
#: and the shape `_validate_output` checks — so the schema the model is
#: asked for and the schema the envelope publishes cannot drift apart.
#: `tests/golden/recommendation_tool_schema.json` pins it, so editing the
#: contract with the model is a deliberate, reviewable act.
RECOMMENDATION_TOOL_NAME = "record_investment_recommendation"
RECOMMENDATION_TOOL = {
    "name": RECOMMENDATION_TOOL_NAME,
    "description": (
        "Record the final BUY / NEGOTIATE / REJECT call for this residential "
        "property, with the evidence that drove it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "recommendation": {
                "type": "string",
                "enum": list(RECOMMENDATIONS),
                "description": "The investment call.",
            },
            "confidence_percent": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": (
                    "How confident the call is, lowered in proportion to "
                    "whatever evidence was missing."
                ),
            },
            "key_strengths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Short points in the property's favour.",
            },
            "key_concerns": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Short points against it. Name any evidence gaps here."
                ),
            },
            "recommended_offer_price_inr": {
                "type": "number",
                "description": (
                    "Offer to counter with, in INR. Omit unless an asking "
                    "price was supplied."
                ),
            },
        },
        "required": [
            "recommendation",
            "confidence_percent",
            "key_strengths",
            "key_concerns",
        ],
    },
}

SYSTEM_PROMPT = (
    "You are a conservative residential real-estate investment analyst in India. "
    "You will be given an 'evidence' object built from whatever the caller had "
    "available -- it may be partial. Missing evidence is not an error; reason with "
    "what's there, and lower your confidence_percent in proportion to what's "
    "missing rather than guessing. Record your answer by calling "
    f"{RECOMMENDATION_TOOL_NAME}, and list any evidence gaps among key_concerns."
)

VALID_RISK_LEVELS = ("Low", "Medium", "High")
# Cap serialized evidence so a caller cannot dump an unbounded object into
# the prompt. Normal capability outputs are a few KB at most.
MAX_EVIDENCE_BYTES = 8_192


def recommend(
    *,
    property_intelligence: dict[str, Any] | None = None,
    financial_analysis: dict[str, Any] | None = None,
    location_infrastructure_analysis: dict[str, Any] | None = None,
    risk_assessment: dict[str, Any] | None = None,
    financial_score: float | None = None,
    location_score: float | None = None,
    overall_risk: str | None = None,
    asking_price_inr: float | None = None,
    budget: DeadlineBudget | None = None,
) -> dict[str, Any]:
    evidence = _build_evidence(
        property_intelligence=property_intelligence,
        financial_analysis=financial_analysis,
        location_infrastructure_analysis=location_infrastructure_analysis,
        risk_assessment=risk_assessment,
        financial_score=financial_score,
        location_score=location_score,
        overall_risk=overall_risk,
        asking_price_inr=asking_price_inr,
    )
    if not evidence:
        raise ValueError(
            "at least one of property_intelligence, financial_analysis, "
            "location_infrastructure_analysis, risk_assessment, financial_score, "
            "location_score, or overall_risk is required"
        )
    _enforce_evidence_size(evidence)

    parsed = research(
        system=SYSTEM_PROMPT,
        prompt=json.dumps({"evidence": evidence}),
        tool=RECOMMENDATION_TOOL,
        timeout=budget.for_call(1) if budget is not None else 90.0,
    )
    return _validate_output(parsed)


def _enforce_evidence_size(evidence: dict[str, Any]) -> None:
    size = len(json.dumps(evidence, default=str).encode("utf-8"))
    if size > MAX_EVIDENCE_BYTES:
        raise ValueError(
            f"evidence is {size} bytes; maximum is {MAX_EVIDENCE_BYTES}. "
            "Pass the raw capability outputs, not oversized dumps."
        )


def _build_evidence(
    *,
    property_intelligence: dict[str, Any] | None,
    financial_analysis: dict[str, Any] | None,
    location_infrastructure_analysis: dict[str, Any] | None,
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

    if property_intelligence:
        evidence["property_intelligence"] = property_intelligence

    if financial_analysis:
        evidence["financial_analysis"] = financial_analysis
    resolved_financial_score = financial_score
    if resolved_financial_score is None and financial_analysis:
        resolved_financial_score = financial_analysis.get("financial_score")
    if resolved_financial_score is not None:
        evidence["financial_score"] = resolved_financial_score

    if location_infrastructure_analysis:
        evidence["location_infrastructure_analysis"] = location_infrastructure_analysis
    resolved_location_score = location_score
    if resolved_location_score is None and location_infrastructure_analysis:
        resolved_location_score = location_infrastructure_analysis.get("location_score")
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
    """Checks the model's tool arguments against what the manifest publishes.

    A missing or off-enum `recommendation` used to become "NEGOTIATE", and a
    missing `confidence_percent` used to become 50 -- an answer the model
    never gave, returned `ok: true` and indistinguishable from one it did.
    Both fields are `required` in RECOMMENDATION_TOOL, so their absence is a
    model failure; ConnectionError reports it as retryable `unavailable`,
    which is what it is.
    """
    recommendation = parsed.get("recommendation")
    if recommendation not in RECOMMENDATIONS:
        raise ConnectionError(
            f"Claude returned recommendation {recommendation!r}; expected one of "
            f"{', '.join(RECOMMENDATIONS)}"
        )

    raw_confidence = parsed.get("confidence_percent")
    if isinstance(raw_confidence, bool) or not isinstance(raw_confidence, (int, float)):
        raise ConnectionError(
            f"Claude returned confidence_percent {raw_confidence!r}; expected a number 0-100"
        )
    # Clamping a number the model *did* supply keeps its judgment; the
    # schema already asks for 0-100, so this only catches an overshoot.
    confidence = max(0, min(100, int(raw_confidence)))

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
