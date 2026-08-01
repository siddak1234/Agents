#!/usr/bin/env python3
"""Case Preparation Gap Detection Agent — protocol adapter."""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

PROTOCOL = "agentcall/v1"
AGENT_NAME = "case-preparation-gap-detection"

# Mirrors agent.yaml by hand. `agents check` fails if the two disagree.
CAPABILITIES = (
    ("describe", "Report this agent's name and capabilities. Costs nothing."),
    ("review_case", "Analyse case documents and return a readiness score, "
                     "missing evidence, and timeline issues. Rule-based only."),
)


def main() -> int:
    # RULE 1. Redirect stdout before anything else can print to it.
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

    if capability == "review_case":
        return _review_case(capability, payload)

    declared = ", ".join(n for n, _ in CAPABILITIES)
    return fail(capability, "invalid_request", f"unknown capability; this agent offers: {declared}")


def _review_case(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    # RULE 4: validate our own input — the schema in agent.yaml is documentation only.
    case_type = payload.get("case_type")
    if not isinstance(case_type, str) or not case_type.strip():
        return fail(capability, "invalid_request", "'case_type' must be a non-empty string")

    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        return fail(capability, "invalid_request", "'documents' must be a non-empty array")

    for doc in documents:
        if not isinstance(doc, dict):
            return fail(capability, "invalid_request", "each document must be an object")
        if not isinstance(doc.get("id"), str) or not doc["id"].strip():
            return fail(capability, "invalid_request", "each document needs a non-empty string 'id'")
        if not isinstance(doc.get("text"), str) or not doc["text"].strip():
            return fail(capability, "invalid_request", f"document '{doc.get('id')}' needs non-empty 'text'")

    from pipeline import review_case  # lazy import — rule 6

    result = review_case(case_type, documents)
    return ok(capability, result)


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


def zero_usage() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "cost_micros": 0}


if __name__ == "__main__":
    raise SystemExit(main())