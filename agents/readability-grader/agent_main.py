#!/usr/bin/env python3
"""readability-grader — an agentcall/v1 agent.

Scores a block of text for reading difficulty (Flesch-Kincaid grade level
and Flesch Reading Ease) and returns a plain-language verdict. Standard
library only — no credentials, no network, no external dependency.

Try it directly:

    echo '{"protocol":"agentcall/v1","capability":"grade_text","input":{"text":"The cat sat on the mat."}}' \\
      | python3 agent_main.py

The contract lives in docs/AGENT_PROTOCOL.md at the repository root.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

import readability

PROTOCOL = "agentcall/v1"
AGENT_NAME = "readability-grader"

CAPABILITIES = (
    ("describe", "Report this agent's name and capabilities. Costs nothing."),
    (
        "grade_text",
        "Score a block of text for reading difficulty and return a US "
        "grade-level estimate plus a plain verdict.",
    ),
)


def main() -> int:
    # RULE 1: stdout carries the envelope and nothing else.
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

    # RULE 2: exit 0 whenever an envelope was produced, including failures.
    return 0


def dispatch(raw: str) -> dict[str, Any]:  # noqa: PLR0911
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
        return ok(
            capability,
            {
                "name": AGENT_NAME,
                "protocol": PROTOCOL,
                "capabilities": [{"name": n, "description": d} for n, d in CAPABILITIES],
            },
        )

    if capability == "grade_text":
        return _grade_text(capability, payload)

    declared = ", ".join(n for n, _ in CAPABILITIES)
    return fail(capability, "invalid_request", f"unknown capability; this agent offers: {declared}")


def _grade_text(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    # RULE 4: validate your own input, and say precisely what was wrong.
    # The schema in agent.yaml is documentation, not enforcement.
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return fail(capability, "invalid_request", "'text' must be a non-empty string")

    target_grade = payload.get("target_grade")
    if target_grade is not None and not isinstance(target_grade, (int, float)):
        return fail(capability, "invalid_request", "'target_grade' must be a number if provided")

    try:
        result = readability.analyze(text, target_grade=target_grade)
    except ValueError as exc:
        return fail(capability, "invalid_request", str(exc))

    return ok(capability, result)


# This agent has no `unavailable` failure mode: it calls no external
# service, database, or credential-gated dependency. Nothing here can be
# "down" or "missing configuration" — see README.md.


def ok(capability: str, output: dict[str, Any], usage: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "ok": True,
        "capability": capability,
        "output": output,
        "usage": usage or zero_usage(),
        "error": None,
    }


def fail(capability: str, etype: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "ok": False,
        "capability": capability,
        "output": None,
        "usage": zero_usage(),
        "error": {"type": etype, "message": message, "retryable": retryable},
    }


def zero_usage() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "model": None}


if __name__ == "__main__":
    raise SystemExit(main())
