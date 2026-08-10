#!/usr/bin/env python3
"""loan-emi-eligibility: EMI calculation and FOIR-based loan eligibility."""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

PROTOCOL = "agentcall/v1"
AGENT_NAME = "loan-emi-eligibility"

# Mirrors agent.yaml by hand. `agents check` fails if the two disagree.
CAPABILITIES = (
    ("describe", "Report this agent's name and capabilities. Costs nothing."),
    (
        "calculate_emi",
        "Compute the monthly EMI, total payment, and total interest for a loan.",
    ),
    (
        "check_eligibility",
        "Check a requested loan's EMI against the applicant's FOIR limit and "
        "return the maximum eligible principal.",
    ),
)


def main() -> int:
    # RULE 1. Do this before anything else can print.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        envelope = dispatch(sys.stdin.read())
    except Exception as exc:  # noqa: BLE001 -- an envelope is mandatory
        traceback.print_exc(file=sys.stderr)
        envelope = fail("", "internal", f"{type(exc).__name__}: {exc}")
    finally:
        sys.stdout = real_stdout

    json.dump(envelope, real_stdout)
    real_stdout.write("\n")
    real_stdout.flush()
    return 0  # RULE 2: an envelope was produced.


def dispatch(raw: str) -> dict[str, Any]:
    try:
        request = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        return fail("", "invalid_request", f"stdin is not valid JSON: {exc}")
    if not isinstance(request, dict):
        return fail("", "invalid_request", "request must be a JSON object")
    if request.get("protocol") != PROTOCOL:
        return fail("", "invalid_request", f"unsupported protocol {request.get('protocol')!r}")

    capability = request.get("capability")
    if not isinstance(capability, str):
        return fail("", "invalid_request", "missing 'capability'")

    payload = request.get("input") or {}
    if not isinstance(payload, dict):
        return fail(capability, "invalid_request", "'input' must be an object")

    if capability == "describe":
        return ok(capability, {
            "name": AGENT_NAME,
            "protocol": PROTOCOL,
            "capabilities": [{"name": n, "description": d} for n, d in CAPABILITIES],
        })

    if capability == "calculate_emi":
        return _handle_calculate_emi(payload)

    if capability == "check_eligibility":
        return _handle_check_eligibility(payload)

    declared = ", ".join(n for n, _ in CAPABILITIES)
    return fail(capability, "invalid_request", f"unknown capability; this agent offers: {declared}")


def _handle_calculate_emi(payload: dict[str, Any]) -> dict[str, Any]:
    import loan_logic  # imported lazily per rule 6; cheap here, but consistent

    try:
        result = loan_logic.calculate_emi(
            principal=payload.get("principal"),
            annual_rate_percent=payload.get("annual_rate_percent"),
            tenure_months=payload.get("tenure_months"),
        )
    except ValueError as exc:
        return fail("calculate_emi", "invalid_request", str(exc))
    return ok("calculate_emi", result)


def _handle_check_eligibility(payload: dict[str, Any]) -> dict[str, Any]:
    import loan_logic

    # Default to "standard" only when the key is absent. If the caller sent
    # applicant_tier explicitly -- including null -- that must be validated
    # as-is and rejected if it isn't a real tier, not silently relaxed to
    # the default tier. loan_logic.check_eligibility no longer coerces
    # invalid values itself, so this is the only place the default applies.
    if "applicant_tier" in payload:
        tier = payload["applicant_tier"]
    else:
        tier = loan_logic.DEFAULT_TIER

    try:
        result = loan_logic.check_eligibility(
            monthly_income=payload.get("monthly_income"),
            existing_emis=payload.get("existing_emis"),
            requested_principal=payload.get("requested_principal"),
            annual_rate_percent=payload.get("annual_rate_percent"),
            tenure_months=payload.get("tenure_months"),
            applicant_tier=tier,
        )
    except ValueError as exc:
        return fail("check_eligibility", "invalid_request", str(exc))
    return ok("check_eligibility", result)


def ok(capability: str, output: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL, "ok": True, "capability": capability,
        "output": output, "usage": zero_usage(), "error": None,
    }


def fail(capability: str, etype: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL, "ok": False, "capability": capability,
        "output": None, "usage": zero_usage(),
        "error": {"type": etype, "message": message, "retryable": retryable},
    }


def zero_usage() -> dict[str, Any]:
    return {"input_tokens": 0, "output_tokens": 0, "model": None}


if __name__ == "__main__":
    raise SystemExit(main())
