#!/usr/bin/env python3
"""Protocol adapter for the patient-appointment-agent.

Reads one agentcall/v1 JSON request on stdin, delegates to the logic
modules, and writes exactly one JSON envelope to stdout. No business
logic lives here.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

PROTOCOL = "agentcall/v1"
AGENT_NAME = "patient-appointment-agent"

# Mirrors agent.yaml capability names. `agents check` fails when they disagree.
CAPABILITIES = (
    ("describe",            "Report this agent's name and capabilities. Costs nothing."),
    ("list_slots",          "Return open appointment slots, optionally filtered by specialty, physician, or date."),
    ("book_appointment",    "Reserve a specific slot for a named patient."),
    ("cancel_appointment",  "Cancel a previously confirmed booking by appointment ID."),
    ("chat",                "Understand a free-form patient message and suggest a scheduling action. Requires ANTHROPIC_API_KEY."),
)


def main() -> int:
    # RULE 1 — redirect stdout before any work so no stray print can corrupt the envelope.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        envelope = dispatch(sys.stdin.read())
    except Exception as exc:          # noqa: BLE001 — an envelope is always required
        traceback.print_exc(file=sys.stderr)
        envelope = fail("", "internal", f"{type(exc).__name__}: {exc}")
    finally:
        sys.stdout = real_stdout

    json.dump(envelope, real_stdout)
    real_stdout.write("\n")
    real_stdout.flush()
    return 0                          # RULE 2 — exit 0 whenever an envelope was produced


def dispatch(raw: str) -> dict[str, Any]:
    # --- protocol-level validation (brief §"All of these are invalid_request") ---
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
        return fail("", "invalid_request", "missing or non-string 'capability'")

    payload = request.get("input") or {}
    if not isinstance(payload, dict):
        return fail(capability, "invalid_request", "'input' must be a JSON object")

    # --- capability dispatch ---
    if capability == "describe":
        return ok(capability, {
            "name": AGENT_NAME,
            "protocol": PROTOCOL,
            "capabilities": [{"name": n, "description": d} for n, d in CAPABILITIES],
        })

    # RULE 6 — heavy imports live inside the handler that needs them, not at the top.
    if capability == "list_slots":
        return _handle_list_slots(capability, payload)

    if capability == "book_appointment":
        return _handle_book_appointment(capability, payload)

    if capability == "cancel_appointment":
        return _handle_cancel_appointment(capability, payload)

    if capability == "chat":
        return _handle_chat(capability, payload)

    declared = ", ".join(n for n, _ in CAPABILITIES)
    return fail(capability, "invalid_request",
                f"unknown capability; this agent offers: {declared}")


# ---------------------------------------------------------------------------
# Handlers — one per capability, each a thin translator.
# ---------------------------------------------------------------------------

def _handle_list_slots(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    specialty   = payload.get("specialty")
    doctor_name = payload.get("doctor_name")
    date        = payload.get("date")

    # RULE 4 — validate types; schema in agent.yaml is documentation only.
    if specialty is not None and not isinstance(specialty, str):
        return fail(capability, "invalid_request", "'specialty' must be a string")
    if doctor_name is not None and not isinstance(doctor_name, str):
        return fail(capability, "invalid_request", "'doctor_name' must be a string")
    if date is not None and not isinstance(date, str):
        return fail(capability, "invalid_request", "'date' must be a string")
    if date:
        import datetime
        try:
            datetime.date.fromisoformat(date)
        except ValueError:
            return fail(capability, "invalid_request",
                        f"'date' must be in YYYY-MM-DD format, got {date!r}")

    from appointment_manager import list_available_slots
    slots = list_available_slots(specialty=specialty, doctor_name=doctor_name, date=date)
    return ok(capability, {"slots": slots})


def _handle_book_appointment(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    slot_id       = payload.get("slot_id")
    patient_name  = payload.get("patient_name")
    patient_phone = payload.get("patient_phone")
    reason        = payload.get("reason")

    # Required fields
    if not isinstance(slot_id, str) or not slot_id.strip():
        return fail(capability, "invalid_request",
                    "'slot_id' is required and must be a non-empty string")
    if not isinstance(patient_name, str) or not patient_name.strip():
        return fail(capability, "invalid_request",
                    "'patient_name' is required and must be a non-empty string")
    if not isinstance(patient_phone, str) or not patient_phone.strip():
        return fail(capability, "invalid_request",
                    "'patient_phone' is required and must be a non-empty string")
    if reason is not None and not isinstance(reason, str):
        return fail(capability, "invalid_request", "'reason' must be a string")

    from appointment_manager import book_appointment
    try:
        appt = book_appointment(
            slot_id=slot_id.strip(),
            patient_name=patient_name.strip(),
            patient_phone=patient_phone.strip(),
            reason=reason,
        )
    except ValueError as exc:
        return fail(capability, "invalid_request", str(exc))

    return ok(capability, {
        "appointment_id": appt["appointment_id"],
        "status":         appt["status"],
        "doctor_name":    appt["doctor_name"],
        "date":           appt["date"],
        "time":           appt["time"],
        "patient_name":   appt["patient_name"],
    })


def _handle_cancel_appointment(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    appt_id = payload.get("appointment_id")
    if not isinstance(appt_id, str) or not appt_id.strip():
        return fail(capability, "invalid_request",
                    "'appointment_id' is required and must be a non-empty string")

    from appointment_manager import cancel_appointment
    try:
        result = cancel_appointment(appt_id.strip())
    except ValueError as exc:
        return fail(capability, "invalid_request", str(exc))

    return ok(capability, {
        "appointment_id": result["appointment_id"],
        "status":         result["status"],
    })


def _handle_chat(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    message      = payload.get("message")
    chat_history = payload.get("chat_history")

    if not isinstance(message, str) or not message.strip():
        return fail(capability, "invalid_request",
                    "'message' is required and must be a non-empty string")

    if chat_history is not None:
        if not isinstance(chat_history, list):
            return fail(capability, "invalid_request", "'chat_history' must be an array")
        for turn in chat_history:
            if not isinstance(turn, dict):
                return fail(capability, "invalid_request",
                            "each item in 'chat_history' must be an object")
            if turn.get("role") not in ("user", "assistant") or \
               not isinstance(turn.get("content"), str):
                return fail(capability, "invalid_request",
                            "each chat_history item needs role ('user'/'assistant') "
                            "and content (string)")

    # RULE 3 — use .get(), never os.environ["KEY"]
    import os
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return fail(capability, "unavailable",
                    "ANTHROPIC_API_KEY is not set; set it to use the chat capability",
                    retryable=False)

    from llm_client import call_claude_chat
    try:
        output, usage = call_claude_chat(message.strip(), chat_history)
    except RuntimeError as exc:
        msg = str(exc)
        if "401" in msg or "403" in msg:
            return fail(capability, "unavailable",
                        f"Anthropic API rejected the key: {msg}", retryable=False)
        if "timeout" in msg.lower():
            return fail(capability, "timeout",
                        f"Anthropic API timed out: {msg}", retryable=True)
        return fail(capability, "internal",
                    f"Unexpected error from Anthropic API: {msg}", retryable=False)

    return ok(capability, {
        "response":               output.get("response", ""),
        "suggested_action":       output.get("suggested_action", "none"),
        "suggested_action_input": output.get("suggested_action_input"),
    }, usage=usage)


# ---------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------

def ok(capability: str, output: dict[str, Any],
       usage: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "protocol":   PROTOCOL,
        "ok":         True,
        "capability": capability,
        "output":     output,
        "usage":      usage if usage is not None else zero_usage(),
        "error":      None,
    }


def fail(capability: str, etype: str, message: str,
         *, retryable: bool = False) -> dict[str, Any]:
    return {
        "protocol":   PROTOCOL,
        "ok":         False,
        "capability": capability,
        "output":     None,
        "usage":      zero_usage(),
        "error":      {"type": etype, "message": message, "retryable": retryable},
    }


def zero_usage() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "cost_micros": 0}


if __name__ == "__main__":
    raise SystemExit(main())
