#!/usr/bin/env python3
"""Investment Due Diligence Agent -- protocol adapter.

Reads one agentcall/v1 request from stdin, validates its shape, routes it
to a small per-capability handler, and writes exactly one response
envelope to stdout. No business logic lives in this file -- each handler
below only translates between the wire payload and `analysis.py` /
`recommendation.py`.

Business logic signals failure with plain built-in exceptions instead of
a custom error hierarchy:
  ValueError      -> invalid_request  (bad/insufficient input)
  RuntimeError    -> unavailable, not retryable (missing key, a 4xx)
  ConnectionError -> unavailable, retryable     (unreachable, a 5xx/429)
  TimeoutError    -> timeout, retryable         (call didn't finish in time)
This dispatch function is the only place that mapping happens.
"""

from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Callable
from typing import Any

from budget import DeadlineBudget

PROTOCOL = "agentcall/v1"
AGENT_NAME = "investment-due-diligence"

# Mirrors agent.yaml by hand. `agents check` fails if the two disagree.
CAPABILITIES = (
    ("describe", "Report this agent's name and capabilities. Costs nothing."),
    ("property_intelligence", "Build a verified property profile from the supplied listing details."),
    ("financial_analysis", "Evaluate the property's financial attractiveness."),
    ("location_infrastructure_analysis", "Assess connectivity, amenities, and growth potential of the locality."),
    ("risk_assessment", "Identify builder, legal, environmental, and market risks."),
    ("investment_recommendation", "Synthesize prior findings into a BUY/NEGOTIATE/REJECT recommendation."),
)

#: Capabilities that make no outbound call, so there is no deadline to
#: divide and no malformed `deadline_ms` worth failing over -- `describe`
#: is documented as costing nothing and reads neither payload nor budget.
BUDGET_FREE = frozenset({"describe"})


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
    _reset_spend()
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

    handler = HANDLERS.get(capability)
    if handler is None:
        declared = ", ".join(n for n, _ in CAPABILITIES)
        return fail(capability, "invalid_request", f"unknown capability; this agent offers: {declared}")

    try:
        budget = (
            None
            if capability in BUDGET_FREE
            else DeadlineBudget.from_deadline_ms(request.get("deadline_ms"))
        )
        output = handler(payload, budget)
    except ValueError as exc:
        return fail(capability, "invalid_request", str(exc))
    except TimeoutError as exc:
        return fail(capability, "timeout", str(exc), retryable=True)
    except ConnectionError as exc:
        return fail(capability, "unavailable", str(exc), retryable=True)
    except RuntimeError as exc:
        return fail(capability, "unavailable", str(exc), retryable=False)

    return ok(capability, output, _spent_usage())


# ---------------------------------------------------------------------------
# Per-capability handlers. Each one validates the shape of its own input
# (RULE 4), calls into `analysis` / `recommendation`, and returns that
# capability's output object. What it cost is not threaded back through
# here -- clients.py records spend as it happens and dispatch reads it
# once, which is what keeps a post-payment failure honest. The actual work
# -- search, scoring, LLM calls -- lives entirely outside this file.
# ---------------------------------------------------------------------------

Handler = Callable[[dict[str, Any], "DeadlineBudget | None"], dict[str, Any]]


def _require_str(payload: dict[str, Any], key: str, *, required: bool = True) -> str | None:
    value = payload.get(key)
    if value is None:
        if required:
            raise ValueError(f"'{key}' is required")
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' must be a non-empty string")
    return value


def _require_number(payload: dict[str, Any], key: str, *, required: bool = True) -> float | None:
    value = payload.get(key)
    if value is None:
        if required:
            raise ValueError(f"'{key}' is required")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"'{key}' must be a number")
    return float(value)


def _require_object(payload: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"'{key}' must be an object")
    return value


def _handle_describe(
    payload: dict[str, Any], budget: DeadlineBudget | None
) -> dict[str, Any]:
    del payload, budget
    output = {
        "name": AGENT_NAME,
        "protocol": PROTOCOL,
        "capabilities": [{"name": n, "description": d} for n, d in CAPABILITIES],
    }
    return output


def _handle_property_intelligence(
    payload: dict[str, Any], budget: DeadlineBudget | None
) -> dict[str, Any]:
    from analysis import build_property_profile  # RULE 6: imported only when this capability runs

    output = build_property_profile(
        property_url=_require_str(payload, "property_url", required=False),
        address=_require_str(payload, "address", required=False),
        asking_price_inr=_require_number(payload, "asking_price_inr", required=False),
        area_sqft=_require_number(payload, "area_sqft", required=False),
        budget=budget,
    )
    return output


def _handle_financial_analysis(
    payload: dict[str, Any], budget: DeadlineBudget | None
) -> dict[str, Any]:
    from analysis import analyze_financials

    output = analyze_financials(
        location=_require_str(payload, "location"),
        price_per_sqft=_require_number(payload, "price_per_sqft", required=False),
        asking_price_inr=_require_number(payload, "asking_price_inr", required=False),
        area_sqft=_require_number(payload, "area_sqft", required=False),
        budget=budget,
    )
    return output


def _handle_location_infrastructure_analysis(
    payload: dict[str, Any], budget: DeadlineBudget | None
) -> dict[str, Any]:
    from analysis import analyze_location

    output = analyze_location(location=_require_str(payload, "location"), budget=budget)
    return output


def _handle_risk_assessment(
    payload: dict[str, Any], budget: DeadlineBudget | None
) -> dict[str, Any]:
    from analysis import assess_risk

    output = assess_risk(
        builder=_require_str(payload, "builder", required=False),
        location=_require_str(payload, "location"),
        property_type=_require_str(payload, "property_type", required=False),
        budget=budget,
    )
    return output


def _handle_investment_recommendation(
    payload: dict[str, Any], budget: DeadlineBudget | None
) -> dict[str, Any]:
    from recommendation import recommend

    output = recommend(
        property_intelligence=_require_object(payload, "property_intelligence"),
        financial_analysis=_require_object(payload, "financial_analysis"),
        location_infrastructure_analysis=_require_object(
            payload, "location_infrastructure_analysis"
        ),
        risk_assessment=_require_object(payload, "risk_assessment"),
        financial_score=_require_number(payload, "financial_score", required=False),
        location_score=_require_number(payload, "location_score", required=False),
        overall_risk=_require_str(payload, "overall_risk", required=False),
        asking_price_inr=_require_number(payload, "asking_price_inr", required=False),
        budget=budget,
    )
    return output


HANDLERS: dict[str, Handler] = {
    "describe": _handle_describe,
    "property_intelligence": _handle_property_intelligence,
    "financial_analysis": _handle_financial_analysis,
    "location_infrastructure_analysis": _handle_location_infrastructure_analysis,
    "risk_assessment": _handle_risk_assessment,
    "investment_recommendation": _handle_investment_recommendation,
}


# ---------------------------------------------------------------------------
# Envelope builders.
# ---------------------------------------------------------------------------

def ok(capability: str, output: dict[str, Any], usage: dict[str, int]) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL, "ok": True, "capability": capability,
        "output": output, "usage": usage, "error": None,
    }


def fail(capability: str, etype: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL, "ok": False, "capability": capability,
        "output": None, "usage": _spent_usage(),
        "error": {"type": etype, "message": message, "retryable": retryable},
    }


def zero_usage() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "cost_micros": 0}


# ---------------------------------------------------------------------------
# Spend. Tavily bills per search and returns no tokens, so the four search
# capabilities have a real `cost_micros` and no token counts at all. Both
# helpers reach clients.py through `sys.modules` rather than importing it:
# a module `describe` never loaded cannot have spent anything, and asking
# through the module table keeps that path free of clients.py entirely
# (RULE 6, the same reason the capability imports are lazy).
# ---------------------------------------------------------------------------

def _reset_spend() -> None:
    clients = sys.modules.get("clients")
    if clients is not None:
        clients.reset_spend()


def _spent_usage() -> dict[str, int]:
    """What this request spent, read once where the envelope is built.

    Both success and failure go through here, so a request that paid for
    two searches and then timed out reports those two searches --
    docs/AGENT_PROTOCOL.md zeroes `usage` only when nothing was spent.
    """
    clients = sys.modules.get("clients")
    return clients.spent_usage() if clients is not None else zero_usage()


if __name__ == "__main__":
    raise SystemExit(main())
