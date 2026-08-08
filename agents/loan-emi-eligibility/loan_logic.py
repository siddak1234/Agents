"""EMI calculation and FOIR-based loan eligibility.

Imports nothing about the agentcall protocol. Every function here raises
ValueError on bad input, with a message naming what was wrong; the protocol
adapter (agent_main.py) is responsible for turning that into an
invalid_request envelope.
"""

from __future__ import annotations

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


def _validate_positive_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"'{field_name}' must be a number")
    if value <= 0:
        raise ValueError(f"'{field_name}' must be greater than 0")
    return float(value)


def _validate_non_negative_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"'{field_name}' must be a number")
    if value < 0:
        raise ValueError(f"'{field_name}' must be greater than or equal to 0")
    return float(value)


def _validate_positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"'{field_name}' must be an integer")
    if value <= 0:
        raise ValueError(f"'{field_name}' must be greater than 0")
    return value


def calculate_emi(principal: float, annual_rate_percent: float, tenure_months: int) -> dict[str, float]:
    """Standard reducing-balance EMI formula.

    EMI = P * r * (1+r)^n / ((1+r)^n - 1), where r is the monthly rate.
    """
    principal = _validate_positive_number(principal, "principal")
    annual_rate_percent = _validate_positive_number(annual_rate_percent, "annual_rate_percent")
    tenure_months = _validate_positive_int(tenure_months, "tenure_months")

    monthly_rate = (annual_rate_percent / 12.0) / 100.0
    growth = (1.0 + monthly_rate) ** tenure_months
    emi = principal * monthly_rate * growth / (growth - 1.0)

    total_payment = emi * tenure_months
    total_interest = total_payment - principal

    return {
        "emi": round(emi, 2),
        "total_payment": round(total_payment, 2),
        "total_interest": round(total_interest, 2),
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
    monthly_income = _validate_positive_number(monthly_income, "monthly_income")
    existing_emis = _validate_non_negative_number(existing_emis, "existing_emis")
    requested_principal = _validate_positive_number(requested_principal, "requested_principal")
    annual_rate_percent = _validate_positive_number(annual_rate_percent, "annual_rate_percent")
    tenure_months = _validate_positive_int(tenure_months, "tenure_months")

    if not isinstance(applicant_tier, str) or not applicant_tier.strip():
        applicant_tier = DEFAULT_TIER
    if applicant_tier not in FOIR_LIMITS_PERCENT:
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
        # Invert the EMI formula for principal.
        max_eligible_principal = max_allowed_new_emi * (growth - 1.0) / (monthly_rate * growth)
        max_eligible_principal = round(max(max_eligible_principal, 0.0), 2)

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
