"""EMI calculation and FOIR-based loan eligibility.

Imports nothing about the agentcall protocol. Every function here raises
ValueError on bad input, with a message naming what was wrong; the protocol
adapter (agent_main.py) is responsible for turning that into an
invalid_request envelope.
"""

from __future__ import annotations

import math

# The owned rulebook: FOIR (Fixed Obligation to Income Ratio) limit per
# applicant tier. This is the knowledge a caller should not have to carry
# themselves -- it is the whole reason this agent exists rather than being
# EMI math inlined at every call site.
FOIR_LIMITS_PERCENT: dict[str, float] = {
    "standard": 50.0,
    "salaried_premium": 55.0,
    "self_employed": 40.0,
}

DEFAULT_TIER = "standard"

# Sane maxima. Nothing plausible in Indian retail lending approaches these;
# they exist purely to reject caller unit-mistakes (e.g. tenure sent in days
# instead of months) before they can overflow the EMI formula's (1+r)^n term.
MAX_PRINCIPAL = 1_000_000_000.0  # INR 100 crore
MAX_ANNUAL_RATE_PERCENT = 100.0
MAX_TENURE_MONTHS = 1200  # 100 years


def _validate_positive_number(value: object, field_name: str, *, max_value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"'{field_name}' must be a number")
    if not math.isfinite(value):
        raise ValueError(f"'{field_name}' must be a finite number")
    if value <= 0:
        raise ValueError(f"'{field_name}' must be greater than 0")
    if value > max_value:
        raise ValueError(f"'{field_name}' must be at most {max_value}")
    return float(value)


def _validate_non_negative_number(value: object, field_name: str, *, max_value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"'{field_name}' must be a number")
    if not math.isfinite(value):
        raise ValueError(f"'{field_name}' must be a finite number")
    if value < 0:
        raise ValueError(f"'{field_name}' must be greater than or equal to 0")
    if value > max_value:
        raise ValueError(f"'{field_name}' must be at most {max_value}")
    return float(value)


def _validate_positive_int(value: object, field_name: str, *, max_value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"'{field_name}' must be an integer")
    if value <= 0:
        raise ValueError(f"'{field_name}' must be greater than 0")
    if value > max_value:
        raise ValueError(f"'{field_name}' must be at most {max_value}")
    return value


def calculate_emi(
    principal: float, annual_rate_percent: float, tenure_months: int
) -> dict[str, float]:
    """Standard reducing-balance EMI formula.

    EMI = P * r * (1+r)^n / ((1+r)^n - 1), where r is the monthly rate.
    """
    principal = _validate_positive_number(principal, "principal", max_value=MAX_PRINCIPAL)
    annual_rate_percent = _validate_positive_number(
        annual_rate_percent, "annual_rate_percent", max_value=MAX_ANNUAL_RATE_PERCENT
    )
    tenure_months = _validate_positive_int(
        tenure_months, "tenure_months", max_value=MAX_TENURE_MONTHS
    )

    monthly_rate = (annual_rate_percent / 12.0) / 100.0
    try:
        growth = (1.0 + monthly_rate) ** tenure_months
        emi = principal * monthly_rate * growth / (growth - 1.0)
    except OverflowError as exc:
        # Bounds above should make this unreachable, but a caller-side
        # units mistake is an invalid_request, never an internal crash --
        # this is the second line of defense, not the first.
        raise ValueError(f"inputs produce a value too large to compute: {exc}") from exc
    emi = round(emi, 2)

    # Derive total_payment/total_interest from the *rounded* emi, so the
    # three numbers in one response reconcile with each other exactly --
    # an auditor multiplying emi * tenure_months by hand gets total_payment
    # back precisely, instead of landing a paisa off.
    total_payment = round(emi * tenure_months, 2)
    total_interest = round(total_payment - principal, 2)

    return {
        "emi": emi,
        "total_payment": total_payment,
        "total_interest": total_interest,
    }


def check_eligibility(
    monthly_income: float,
    existing_emis: float,
    requested_principal: float,
    annual_rate_percent: float,
    tenure_months: int,
    applicant_tier: str = DEFAULT_TIER,
) -> dict[str, object]:
    """Check whether the requested loan's EMI fits the applicant's FOIR limit.

    FOIR = (existing_emis + new_emi) / monthly_income * 100.
    Also returns the maximum principal the applicant could borrow at this
    rate/tenure without breaching their tier's FOIR limit.
    """
    monthly_income = _validate_positive_number(
        monthly_income, "monthly_income", max_value=MAX_PRINCIPAL
    )
    existing_emis = _validate_non_negative_number(
        existing_emis, "existing_emis", max_value=MAX_PRINCIPAL
    )
    requested_principal = _validate_positive_number(
        requested_principal, "requested_principal", max_value=MAX_PRINCIPAL
    )
    annual_rate_percent = _validate_positive_number(
        annual_rate_percent, "annual_rate_percent", max_value=MAX_ANNUAL_RATE_PERCENT
    )
    tenure_months = _validate_positive_int(
        tenure_months, "tenure_months", max_value=MAX_TENURE_MONTHS
    )

    # applicant_tier defaults to DEFAULT_TIER only when the *key is absent*
    # from the caller's payload -- that substitution happens in
    # agent_main.py before this function is called. By the time we get
    # here, applicant_tier must already be a valid tier string; a caller
    # that explicitly sends null must get invalid_request, not a silent
    # (and possibly stricter-than-intended) fallback to "standard".
    if not isinstance(applicant_tier, str) or applicant_tier not in FOIR_LIMITS_PERCENT:
        allowed = ", ".join(sorted(FOIR_LIMITS_PERCENT))
        raise ValueError(f"'applicant_tier' must be one of: {allowed}")

    foir_limit_percent = FOIR_LIMITS_PERCENT[applicant_tier]

    emi_result = calculate_emi(requested_principal, annual_rate_percent, tenure_months)
    new_emi = emi_result["emi"]

    total_obligation = existing_emis + new_emi
    foir_percent = round((total_obligation / monthly_income) * 100.0, 2)

    eligible = foir_percent <= foir_limit_percent

    # Max principal such that (existing_emis + emi_for_principal) / income <= limit
    max_allowed_new_emi = (monthly_income * (foir_limit_percent / 100.0)) - existing_emis
    if max_allowed_new_emi <= 0:
        max_eligible_principal = 0.0
    else:
        monthly_rate = (annual_rate_percent / 12.0) / 100.0
        growth = (1.0 + monthly_rate) ** tenure_months
        # Invert the EMI formula for principal. max_allowed_new_emi > 0 and
        # growth > 1 here (rate is validated > 0), so this is provably
        # non-negative -- no clamp needed.
        max_eligible_principal = round(
            max_allowed_new_emi * (growth - 1.0) / (monthly_rate * growth), 2
        )

    if eligible:
        reason = (
            f"FOIR {foir_percent}% is within the {applicant_tier} limit of "
            f"{foir_limit_percent}%."
        )
    else:
        reason = (
            f"FOIR {foir_percent}% exceeds the {applicant_tier} limit of "
            f"{foir_limit_percent}%. Maximum eligible principal at this rate "
            f"and tenure is {max_eligible_principal}."
        )

    return {
        "eligible": eligible,
        "emi": new_emi,
        "foir_percent": foir_percent,
        "foir_limit_percent": foir_limit_percent,
        "max_eligible_principal": max_eligible_principal,
        "reason": reason,
    }
