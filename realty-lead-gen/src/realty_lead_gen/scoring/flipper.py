"""Flipper (fix-and-flip) scoring.

Core investor math, with confidence intervals wherever an input has one.

    MAO = 0.70 * ARV - Rehab_high

If asking price <= MAO, the property is "in the box" — the flipper can
buy at asking and still hit the 70% rule. Score components:

    * deal_gap_ratio       — how far list_price is below MAO (positive)
                             or above (negative).
    * rehab_confidence     — reciprocal-of-cost-spread; wide ranges hurt.
    * arv_confidence       — inherited from valuation.
    * red_flag_penalty     — foundation/roof/structural knock the score.
    * days_on_market_bonus — long DOM implies price flex.
    * condition_grade      — C4/C5 preferred over C2/C3 (mispriced upside).

Final score is a weighted combination clipped to [0, 1]. Explicit
weights, no black-box regression — a realtor should be able to reverse
this in five minutes.

References: FlipperForce "70% rule" and Lima One's investor's guide,
plus standard house-flipping ROI framing.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from realty_lead_gen.scoring.base import PropertyContext, ScoreOutput, clamp_unit

SCORER_VERSION: Final[str] = "flipper_v1"

_WEIGHTS: Final[dict[str, float]] = {
    "deal_gap": 0.45,
    "rehab_confidence": 0.12,
    "arv_confidence": 0.13,
    "red_flag_penalty": 0.10,
    "days_on_market": 0.10,
    "condition_upside": 0.10,
}

# Additional constants derived from research (see ARCHITECTURE.md).
_ARV_TO_MAO_RATIO: Final[Decimal] = Decimal("0.70")
_MAX_RED_FLAGS_PENALTY: Final[Decimal] = Decimal("1.0")
_RED_FLAG_WEIGHT: Final[dict[str, Decimal]] = {
    "foundation": Decimal("0.4"),
    "roof": Decimal("0.25"),
    "structural": Decimal("0.4"),
    "water_damage": Decimal("0.2"),
    "mold": Decimal("0.25"),
    "fire_damage": Decimal("0.35"),
}
_TARGET_MIN_MARGIN: Final[Decimal] = Decimal("0.15")  # 15% target margin over MAO


class FlipperScorer:
    scorer_version: str = SCORER_VERSION

    def score(self, ctx: PropertyContext) -> ScoreOutput:
        components: dict[str, dict[str, object]] = {}

        # --- Compute the four numeric primitives -----------------------------
        arv = ctx.arv_cents or ctx.avm_high_cents
        list_price = ctx.list_price_cents
        rehab_high = ctx.rehab_high_cents or 0
        rehab_low = ctx.rehab_low_cents or 0

        if arv is None or list_price is None:
            return ScoreOutput(
                score=Decimal("0.0000"),
                confidence=Decimal("0.100"),
                components={
                    "insufficient_data": {"reason": "missing arv or list price"},
                },
                rationale="Insufficient data to compute deal quality (missing ARV or list price).",
            )

        mao_cents = int(Decimal(arv) * _ARV_TO_MAO_RATIO) - rehab_high
        # deal gap: positive if list <= mao (good deal), negative otherwise.
        deal_gap_cents = mao_cents - list_price
        # Normalize against ARV so % gap is comparable across price bands.
        deal_gap_ratio = Decimal(deal_gap_cents) / Decimal(max(1, arv))
        deal_gap_component = clamp_unit(
            (deal_gap_ratio - (-_TARGET_MIN_MARGIN)) / (2 * _TARGET_MIN_MARGIN)
        )  # maps -0.15..+0.15 to 0..1
        components["deal_gap"] = {
            "value": float(deal_gap_component),
            "weight": _WEIGHTS["deal_gap"],
            "mao_cents": mao_cents,
            "list_price_cents": list_price,
            "gap_cents": deal_gap_cents,
        }

        # rehab confidence — tighter cost spread => higher confidence
        if rehab_high > 0:
            spread_ratio = Decimal(max(0, rehab_high - rehab_low)) / Decimal(rehab_high)
            rehab_conf = clamp_unit(Decimal("1") - spread_ratio)
        else:
            rehab_conf = Decimal("0.5000")  # unknown => neutral
        components["rehab_confidence"] = {
            "value": float(rehab_conf),
            "weight": _WEIGHTS["rehab_confidence"],
            "spread_cents": rehab_high - rehab_low,
        }

        arv_conf = ctx.avm_confidence if ctx.avm_confidence is not None else Decimal("0.5")
        components["arv_confidence"] = {
            "value": float(arv_conf),
            "weight": _WEIGHTS["arv_confidence"],
        }

        # red flag penalty (subtractive)
        rf_penalty = Decimal("0")
        for flag in ctx.red_flags:
            rf_penalty += _RED_FLAG_WEIGHT.get(flag, Decimal("0.1"))
        rf_penalty = min(rf_penalty, _MAX_RED_FLAGS_PENALTY)
        rf_component = clamp_unit(Decimal("1") - rf_penalty)
        components["red_flag_penalty"] = {
            "value": float(rf_component),
            "weight": _WEIGHTS["red_flag_penalty"],
            "flags": ctx.red_flags,
        }

        # days on market — long DOM => negotiation room
        dom = ctx.days_on_market or 0
        dom_component = clamp_unit(min(Decimal(dom) / Decimal(120), Decimal("1")))
        components["days_on_market"] = {
            "value": float(dom_component),
            "weight": _WEIGHTS["days_on_market"],
            "dom": dom,
        }

        # Condition upside — C4/C5 preferred over C2/C3
        condition_score = _CONDITION_UPSIDE.get(ctx.overall_condition or "", Decimal("0.5"))
        components["condition_upside"] = {
            "value": float(condition_score),
            "weight": _WEIGHTS["condition_upside"],
            "condition": ctx.overall_condition,
        }

        # --- Composite -------------------------------------------------------
        composite = Decimal("0")
        for key, weight in _WEIGHTS.items():
            component_value = Decimal(str(components[key]["value"]))
            composite += component_value * Decimal(str(weight))
        composite = clamp_unit(composite)

        # Confidence is a rough function of AVM + rehab confidence + data completeness
        completeness = Decimal("1.0") if (arv and list_price) else Decimal("0.5")
        confidence = clamp_unit((Decimal(str(arv_conf)) + rehab_conf) / 2 * completeness)

        rationale = (
            f"MAO {mao_cents / 100:,.0f} vs list {list_price / 100:,.0f}; "
            f"gap {deal_gap_cents / 100:+,.0f} ({float(deal_gap_ratio) * 100:.1f}% of ARV); "
            f"rehab spread {rehab_high - rehab_low} cents; "
            f"red flags: {', '.join(ctx.red_flags) or 'none'}; "
            f"DOM {dom}; condition {ctx.overall_condition or 'unknown'}."
        )

        return ScoreOutput(
            score=composite,
            confidence=confidence,
            components=components,
            rationale=rationale,
        )


_CONDITION_UPSIDE: Final[dict[str, Decimal]] = {
    "C1": Decimal("0.20"),  # already pristine — low flip upside
    "C2": Decimal("0.30"),
    "C3": Decimal("0.50"),
    "C4": Decimal("0.85"),  # sweet spot
    "C5": Decimal("0.75"),
    "C6": Decimal("0.35"),  # rehab risk too high
    "NEEDS_HUMAN_REVIEW": Decimal("0.50"),
    "NOT_VISIBLE": Decimal("0.40"),
}
