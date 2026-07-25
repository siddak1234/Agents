"""Signal detection — derive motivated-seller signals from ingested data.

Two paths:
    * Derived signals (from existing snapshots): price cuts, aged
      listings, withdrawn recently, high equity vs assessed value.
      These are computed here from data we already have.
    * Sourced signals (NOD, lis pendens, tax delinquent, code
      violations): come from PropertyRadar / ATTOM / county
      recorder — the source adapter attaches them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from realty_lead_gen.models.signal import SignalKind
from realty_lead_gen.utils.jsontypes import JSONDict

if TYPE_CHECKING:
    from realty_lead_gen.models.property import PropertySnapshot

# --------------------------------------------------------------------------
# Detection thresholds
#
# These are the tunable part of this module and the part a domain expert will
# want to argue with, so they are named and gathered rather than inlined at
# the comparison. Each "saturation" constant is the value at which a signal's
# strength reaches 1.0; strength is always `min(1.0, value / saturation)` so
# the scale stays linear and bounded, and a scorer downstream can compare
# strengths across signal kinds without knowing how each was derived.
#
# Nothing here is calibrated against outcome data yet — they are documented
# starting points, not fitted parameters. Backtesting against closed-lead
# feedback is what should eventually set them.
# --------------------------------------------------------------------------

#: Days on market past which a listing reads as stale to a buyer's agent.
#: Roughly 2x the national median DOM; below that, "still selling" is the
#: better explanation than "seller is motivated".
_AGED_LISTING_DOM_THRESHOLD: Final[int] = 90
_AGED_LISTING_DOM_SATURATION: Final[int] = 200

#: A price history needs at least a first and a last observation before the
#: word "cut" means anything.
_MIN_PRICE_POINTS: Final[int] = 2
#: Below this, a price change is a rounding or a staging adjustment, not a
#: concession. 3% is roughly a single negotiating increment on a median home.
_PRICE_CUT_MIN_FRACTION: Final[float] = 0.03
#: A 25% cut saturates the signal (0.25 * 4 == 1.0).
_PRICE_CUT_STRENGTH_MULTIPLIER: Final[int] = 4

#: Assessed value exceeding last sale price by this fraction implies the
#: owner has room to discount — the classic high-equity flip target.
_HIGH_EQUITY_MIN_RATIO: Final[float] = 0.4

#: Tenure past which owners statistically over-index on life-event moves
#: (downsizing, estate, relocation).
_LONG_TENURE_MIN_YEARS: Final[int] = 15
_LONG_TENURE_SATURATION_YEARS: Final[int] = 40

#: A withdrawn listing is a warm lead only while the decision is still
#: fresh; past two months the seller has usually re-listed or given up.
_WITHDRAWN_RECENCY_DAYS: Final[int] = 60


@dataclass(frozen=True, slots=True)
class SignalCandidate:
    kind: SignalKind
    observed_on: date
    strength: Decimal
    source: str
    payload: JSONDict


class SignalDetectionStep:
    """Compute derived signals from a property's snapshot history."""

    kind = "signals"

    def compute(
        self,
        snapshots: list[PropertySnapshot],
        *,
        as_of: datetime | None = None,
    ) -> list[SignalCandidate]:
        """Return derived signals based on the snapshot timeline."""
        as_of = as_of or datetime.now(UTC)
        as_of_date = as_of.date()
        if not snapshots:
            return []
        # Sort ascending by observed time
        sorted_snaps = sorted(snapshots, key=lambda s: s.observed_at)
        signals: list[SignalCandidate] = []

        # --- Aged listing --------------------------------------------------
        latest = sorted_snaps[-1]
        if (
            latest.days_on_market is not None
            and latest.days_on_market > _AGED_LISTING_DOM_THRESHOLD
        ):
            strength = min(
                Decimal("1.0"),
                Decimal(latest.days_on_market) / Decimal(_AGED_LISTING_DOM_SATURATION),
            )
            signals.append(
                SignalCandidate(
                    kind=SignalKind.aged_listing,
                    observed_on=as_of_date,
                    strength=strength,
                    source="derived",
                    payload={"days_on_market": latest.days_on_market},
                )
            )

        # --- Recent price cut ----------------------------------------------
        active_prices = [
            (s.observed_at.date(), s.list_price_cents)
            for s in sorted_snaps
            if s.list_price_cents is not None
        ]
        if len(active_prices) >= _MIN_PRICE_POINTS:
            # The first observed date is not used: a price cut is dated by
            # when the *new* price appeared, not when the old one did.
            _, first_price = active_prices[0]
            latest_date, latest_price = active_prices[-1]
            if first_price and latest_price and latest_price < first_price:
                drop_pct = (first_price - latest_price) / first_price
                if drop_pct >= _PRICE_CUT_MIN_FRACTION:
                    signals.append(
                        SignalCandidate(
                            kind=SignalKind.recent_price_cut,
                            observed_on=latest_date,
                            strength=min(
                                Decimal("1.0"),
                                Decimal(drop_pct * _PRICE_CUT_STRENGTH_MULTIPLIER).quantize(
                                    Decimal("0.001")
                                ),
                            ),
                            source="derived",
                            payload={
                                "first_price_cents": first_price,
                                "latest_price_cents": latest_price,
                                "drop_pct": float(drop_pct),
                            },
                        )
                    )

        # --- High equity heuristic -----------------------------------------
        if latest.tax_assessed_value_cents and latest.last_sold_price_cents:
            equity_ratio = (latest.tax_assessed_value_cents - latest.last_sold_price_cents) / max(
                1, latest.tax_assessed_value_cents
            )
            if equity_ratio > _HIGH_EQUITY_MIN_RATIO:
                signals.append(
                    SignalCandidate(
                        kind=SignalKind.high_equity,
                        observed_on=as_of_date,
                        strength=Decimal(str(min(1.0, equity_ratio))),
                        source="derived",
                        payload={
                            "assessed_cents": latest.tax_assessed_value_cents,
                            "last_sold_cents": latest.last_sold_price_cents,
                        },
                    )
                )

        # --- Long-term ownership -------------------------------------------
        if latest.last_sold_date is not None:
            years_owned = (as_of_date - latest.last_sold_date).days / 365.25
            if years_owned > _LONG_TENURE_MIN_YEARS:
                strength = min(
                    Decimal("1.0"), Decimal(str(years_owned / _LONG_TENURE_SATURATION_YEARS))
                )
                signals.append(
                    SignalCandidate(
                        kind=SignalKind.long_term_ownership,
                        observed_on=as_of_date,
                        strength=strength,
                        source="derived",
                        payload={"years_owned": years_owned},
                    )
                )

        # --- Withdrawn recently --------------------------------------------
        withdrawn_statuses = {"withdrawn", "canceled", "expired"}
        if latest.status and latest.status.lower() in withdrawn_statuses:
            days_since = (as_of - latest.observed_at).days
            if days_since <= _WITHDRAWN_RECENCY_DAYS:
                signals.append(
                    SignalCandidate(
                        kind=SignalKind.withdrawn_recently,
                        observed_on=as_of_date,
                        strength=Decimal("0.7"),
                        source="derived",
                        payload={"status": latest.status, "days_since": days_since},
                    )
                )

        return signals


def days_since(d: date | None, ref: date | None = None) -> int | None:
    if d is None:
        return None
    ref = ref or (datetime.now(UTC).date() + timedelta(days=0))
    return (ref - d).days
