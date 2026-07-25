"""Wholesaler scoring.

Wholesalers care about **assignability spread** — the delta between the
price they can secure the contract at and what an end-buyer investor
will accept, minus the assignment fee target. They also weight
motivation signals heavily (NOD, tax delinquent, long-term absentee
ownership, probate).

Score components:

    * assignability_spread — (MAO - contract_price - assignment_fee) / ARV
    * motivation_strength  — sum-of-normalized signals with per-kind weights
    * equity_strength      — high equity => more room to negotiate
    * contactability       — right-party-contact probability from skip trace
    * competition_penalty  — recent price cuts / withdrawals imply the
                             market is picking over the property already

Assignment fee target defaults to $10k; can be per-user configured
downstream.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from realty_lead_gen.models.signal import SignalKind
from realty_lead_gen.scoring.base import PropertyContext, ScoreOutput, clamp_unit

SCORER_VERSION: Final[str] = "wholesaler_v1"

_WEIGHTS: Final[dict[str, float]] = {
    "assignability_spread": 0.40,
    "motivation_strength": 0.30,
    "equity_strength": 0.15,
    "contactability": 0.10,
    "competition_penalty": 0.05,
}

_MOTIVATION_WEIGHTS: Final[dict[SignalKind, Decimal]] = {
    SignalKind.nod_filed: Decimal("1.0"),
    SignalKind.lis_pendens: Decimal("0.9"),
    SignalKind.tax_delinquent: Decimal("0.85"),
    SignalKind.code_violation: Decimal("0.6"),
    SignalKind.vacancy_usps: Decimal("0.7"),
    SignalKind.absentee_owner: Decimal("0.5"),
    SignalKind.high_equity: Decimal("0.6"),
    SignalKind.inherited_probate: Decimal("0.85"),
    SignalKind.divorce_filed: Decimal("0.6"),
    SignalKind.bankruptcy: Decimal("0.75"),
    SignalKind.long_term_ownership: Decimal("0.4"),
    SignalKind.recent_price_cut: Decimal("0.5"),
    SignalKind.aged_listing: Decimal("0.4"),
    SignalKind.withdrawn_recently: Decimal("0.7"),
    SignalKind.expired_listing: Decimal("0.6"),
    SignalKind.other: Decimal("0.2"),
}

_ARV_TO_MAO_RATIO: Final[Decimal] = Decimal("0.70")
_DEFAULT_ASSIGNMENT_FEE_CENTS: Final[int] = 10_000_00


class WholesalerScorer:
    scorer_version: str = SCORER_VERSION

    def __init__(
        self,
        assignment_fee_cents: int = _DEFAULT_ASSIGNMENT_FEE_CENTS,
    ) -> None:
        self._fee = assignment_fee_cents

    def score(self, ctx: PropertyContext) -> ScoreOutput:
        components: dict[str, dict[str, object]] = {}

        arv = ctx.arv_cents or ctx.avm_high_cents
        list_price = ctx.list_price_cents
        rehab_high = ctx.rehab_high_cents or 0
        if arv is None or list_price is None:
            return ScoreOutput(
                score=Decimal("0.0000"),
                confidence=Decimal("0.100"),
                components={"insufficient_data": {"reason": "missing arv or list price"}},
                rationale="Insufficient data to compute assignment spread.",
            )

        end_buyer_mao = int(Decimal(arv) * _ARV_TO_MAO_RATIO) - rehab_high
        spread_cents = end_buyer_mao - list_price - self._fee
        spread_ratio = Decimal(spread_cents) / Decimal(max(1, arv))
        # Map -10% .. +10% spread to 0..1
        spread_component = clamp_unit((spread_ratio - Decimal("-0.10")) / Decimal("0.20"))
        components["assignability_spread"] = {
            "value": float(spread_component),
            "weight": _WEIGHTS["assignability_spread"],
            "end_buyer_mao_cents": end_buyer_mao,
            "assignment_fee_cents": self._fee,
            "spread_cents": spread_cents,
        }

        # motivation strength — signals combined via a soft-OR:
        # 1 - product(1 - strength_i * kind_weight_i)
        remainder = Decimal("1")
        signal_details: list[dict[str, object]] = []
        for s in ctx.signals:
            w = _MOTIVATION_WEIGHTS.get(s.kind, Decimal("0.2"))
            contribution = min(Decimal("1"), s.strength * w)
            remainder *= Decimal("1") - contribution
            signal_details.append(
                {
                    "kind": s.kind.value,
                    "weight": float(w),
                    "strength": float(s.strength),
                    "observed_on": s.observed_on.isoformat(),
                }
            )
        motivation = clamp_unit(Decimal("1") - remainder)
        components["motivation_strength"] = {
            "value": float(motivation),
            "weight": _WEIGHTS["motivation_strength"],
            "signals": signal_details,
        }

        equity_component = _has_signal(ctx, SignalKind.high_equity)
        components["equity_strength"] = {
            "value": float(equity_component),
            "weight": _WEIGHTS["equity_strength"],
        }

        # Contactability is set to a neutral default at MVP — populated
        # from ContactChannel.rpc_probability once skip trace is wired.
        contactability = Decimal("0.5")
        components["contactability"] = {
            "value": float(contactability),
            "weight": _WEIGHTS["contactability"],
            "note": "default until skip trace is wired",
        }

        # Competition penalty
        competition_hits = sum(
            1
            for s in ctx.signals
            if s.kind
            in {SignalKind.recent_price_cut, SignalKind.aged_listing, SignalKind.expired_listing}
        )
        competition_component = clamp_unit(
            Decimal("1") - Decimal(competition_hits) * Decimal("0.15")
        )
        components["competition_penalty"] = {
            "value": float(competition_component),
            "weight": _WEIGHTS["competition_penalty"],
            "hits": competition_hits,
        }

        composite = sum(
            Decimal(str(components[k]["value"])) * Decimal(str(w)) for k, w in _WEIGHTS.items()
        )
        composite = clamp_unit(composite)

        confidence = clamp_unit((motivation + (ctx.avm_confidence or Decimal("0.5"))) / 2)

        rationale = (
            f"End-buyer MAO {end_buyer_mao / 100:,.0f}, list {list_price / 100:,.0f}, "
            f"assignment fee {self._fee / 100:,.0f}; spread {spread_cents / 100:+,.0f}; "
            f"{len(ctx.signals)} motivation signal(s) -> {float(motivation):.2f}."
        )

        return ScoreOutput(
            score=composite,
            confidence=confidence,
            components=components,
            rationale=rationale,
        )


def _has_signal(ctx: PropertyContext, kind: SignalKind) -> Decimal:
    matches = [s for s in ctx.signals if s.kind == kind]
    if not matches:
        return Decimal("0.3")
    return max(s.strength for s in matches)
