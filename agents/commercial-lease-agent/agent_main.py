#!/usr/bin/env python3
"""Extracts lease clauses and calculates notice deadlines from commercial leases."""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

import lease_logic

PROTOCOL = "agentcall/v1"
AGENT_NAME = "commercial-lease-agent"          # must equal the folder name

CAPABILITIES = (
    ("describe", "Report this agent's name and capabilities. Costs nothing."),
    ("extract_clauses", "Extract renewal, financial, and maintenance clauses from a lease's pages."),
    ("calculate_deadline", "Calculate a notice deadline from a lease end date and a notice period."),
)


def main() -> int:
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        envelope = dispatch(sys.stdin.read())
    except Exception as exc:  # noqa: BLE001 — an envelope is mandatory
        traceback.print_exc(file=sys.stderr)
        envelope = fail("", "internal", f"{type(exc).__name__}: {exc}")
    finally:
        sys.stdout = real_stdout

    json.dump(envelope, real_stdout)
    real_stdout.write("\n")
    real_stdout.flush()
    return 0


def dispatch(raw: str) -> dict[str, Any]:
    request, parse_error = _parse_request(raw)
    if parse_error is not None:
        return parse_error

    capability = request["capability"]
    handler = _CAPABILITY_HANDLERS.get(capability)
    if handler is None:
        declared = ", ".join(n for n, _ in CAPABILITIES)
        return fail(capability, "invalid_request", f"unknown capability; this agent offers: {declared}")

    return handler(request["input"])


def _parse_request(raw: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return (request, None) on success, or ({}, error_envelope) on failure."""
    try:
        request = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        return {}, fail("", "invalid_request", f"stdin is not valid JSON: {exc}")
    if not isinstance(request, dict):
        return {}, fail("", "invalid_request", "request must be a JSON object")
    if request.get("protocol") != PROTOCOL:
        return {}, fail("", "invalid_request", f"unsupported protocol {request.get('protocol')!r}")

    capability = request.get("capability")
    if not isinstance(capability, str):
        return {}, fail("", "invalid_request", "missing 'capability'")

    payload = request.get("input") or {}
    if not isinstance(payload, dict):
        return {}, fail(capability, "invalid_request", "'input' must be an object")

    return {"capability": capability, "input": payload}, None


def _handle_describe(_payload: dict[str, Any]) -> dict[str, Any]:
    return ok("describe", {
        "name": AGENT_NAME,
        "protocol": PROTOCOL,
        "capabilities": [{"name": n, "description": d} for n, d in CAPABILITIES],
    })


def _handle_extract_clauses(payload: dict[str, Any]) -> dict[str, Any]:
    capability = "extract_clauses"
    lease_pages = payload.get("lease_pages")
    if not isinstance(lease_pages, list) or not lease_pages:
        return fail(capability, "invalid_request", "'lease_pages' must be a non-empty array of strings")
    if not all(isinstance(p, str) for p in lease_pages):
        return fail(capability, "invalid_request", "every item in 'lease_pages' must be a string")

    try:
        clauses, usage = lease_logic.extract_clauses(lease_pages)
    except lease_logic.LeaseLogicError as exc:
        etype, retryable = _EXTRACT_ERROR_MAP.get(type(exc), ("internal", False))
        return fail(capability, etype, str(exc), retryable=retryable, usage=getattr(exc, "usage", None))

    return ok(capability, {"clauses": clauses}, usage=usage)


def _handle_calculate_deadline(payload: dict[str, Any]) -> dict[str, Any]:
    capability = "calculate_deadline"
    lease_end_date = payload.get("lease_end_date")
    notice_period_days = payload.get("notice_period_days")

    if not isinstance(lease_end_date, str) or not lease_end_date.strip():
        return fail(capability, "invalid_request", "'lease_end_date' must be a non-empty string")
    if not isinstance(notice_period_days, int) or isinstance(notice_period_days, bool):
        return fail(capability, "invalid_request", "'notice_period_days' must be an integer")
    if notice_period_days < 0:
        return fail(capability, "invalid_request", "'notice_period_days' must not be negative")

    try:
        deadline = lease_logic.calculate_deadline(lease_end_date, notice_period_days)
    except lease_logic.LeaseLogicError as exc:
        return fail(capability, "invalid_request", str(exc))

    return ok(capability, {"deadline_date": deadline})


# Maps a raised exception type to (error.type, retryable). A missing
# credential or dependency won't fix itself on retry; a model-call failure
# (network blip, transient API error) might.
_EXTRACT_ERROR_MAP: dict[type, tuple[str, bool]] = {
    lease_logic.MissingCredentialError: ("unavailable", False),
    lease_logic.MissingDependencyError: ("unavailable", False),
    lease_logic.ModelCallError: ("unavailable", True),
}

_CAPABILITY_HANDLERS = {
    "describe": _handle_describe,
    "extract_clauses": _handle_extract_clauses,
    "calculate_deadline": _handle_calculate_deadline,
}


def ok(capability: str, output: dict[str, Any], *, usage: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL, "ok": True, "capability": capability,
        "output": output, "usage": usage or zero_usage(), "error": None,
    }


def fail(
    capability: str,
    etype: str,
    message: str,
    *,
    retryable: bool = False,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL, "ok": False, "capability": capability,
        "output": None, "usage": usage or zero_usage(),
        "error": {"type": etype, "message": message, "retryable": retryable},
    }


def zero_usage() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "cost_micros": 0}


if __name__ == "__main__":
    raise SystemExit(main())