#!/usr/bin/env python3
"""A minimal agentcall/v1 agent. Copy this folder to start a new agent.

Standard library only, on purpose: you can run it right now, and it shows the
whole contract in one screen. A real agent brings its own dependencies (see
`realty-lead-gen`), but nothing here requires them.

Try it without the orchestrator at all:

    echo '{"protocol":"agentcall/v1","capability":"describe","input":{}}' \\
      | python3 agent_main.py

The contract lives in docs/AGENT_PROTOCOL.md at the repository root. The four
rules that matter are marked RULE below.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

PROTOCOL = "agentcall/v1"


AGENT_NAME = "operations-maintenance-agent"


CAPABILITIES = (
    ("describe", "Returns metadata describing the Operations Maintenance Agent and its supported capabilities."),
    ("generate_maintenance_plan", "Generates an optimized refinery maintenance plan from operational constraints."),
)


def main() -> int:
    # RULE 1: stdout carries the envelope and nothing else. Point sys.stdout at
    # stderr before doing anything, so a stray print() — yours or a library's —
    # cannot corrupt the response. Write the envelope to the real stdout saved
    # here. This is the rule most often broken by accident.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr

    try:
        envelope = _dispatch(sys.stdin.read())
    except Exception as exc:  # noqa: BLE001 — an envelope is mandatory
        traceback.print_exc(file=sys.stderr)
        envelope = fail("", "internal", f"{type(exc).__name__}: {exc}")
    finally:
        sys.stdout = real_stdout

    json.dump(envelope, real_stdout)
    real_stdout.write("\n")
    real_stdout.flush()

    # RULE 2: exit 0 whenever an envelope was produced, including for failures.
    # A business failure is a successful call that returned a failure. Non-zero
    # means "I could not produce an envelope at all", and the orchestrator
    # turns that into a `transport` error.
    return 0


# Many returns on purpose: each branch is one validation failure or one
# capability. Collapsing them into a single result variable would hide which
# check rejected the request, which is the only thing a caller wants to know.
def _dispatch(raw: str) -> dict[str, Any]:  # noqa: PLR0911
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

    if capability == "generate_maintenance_plan":

        from planner import generate_maintenance_plan
        # RULE 4: validate your own input and say precisely what was wrong. The
        # orchestrator does not validate for you — the schema in agent.yaml is
        # documentation, and two validators would drift.
        try:
            response = generate_maintenance_plan(payload)
            return ok(
                capability,
                response["plan"],
                usage=response["usage"],
            )

        except ValueError as exc:

                return fail(
                    capability,
                    "invalid_request",
                    str(exc)
                )
        

        except Exception as exc:

            message = str(exc)

            if "ANTHROPIC_API_KEY" in message:

                return fail(
                    capability,
                    "unavailable",
                    message,
                    retryable=False
                )

            return fail(
                capability,
                "internal",
                f"Planning failed: {exc}"
            )

    declared = ", ".join(n for n, _ in CAPABILITIES)
    return fail(capability, "invalid_request", f"unknown capability; this agent offers: {declared}")


# RULE 3: a missing credential or unreachable dependency returns
# `unavailable`, never a crash. See agents/realty-lead-gen/'s agentcall.py for
# a real example — no ANTHROPIC_API_KEY disables photo grading instead of
# killing the process. The five error types are: invalid_request, unavailable, timeout,
# internal, transport (transport is orchestrator-side; agents never emit it).


def ok(capability: str, output: dict[str, Any], usage: dict[str, int] | None = None):
    return {
        "protocol": PROTOCOL,
        "ok": True,
        "capability": capability,
        "output": output,
        # Always report usage, zeroed when nothing was spent. Accounting that
        # is optional is accounting that gets forgotten.
        "usage": usage or zero_usage(),
        "error": None,
    }


def fail(capability: str, etype: str, message: str, *, retryable: bool = False):
    return {
        "protocol": PROTOCOL,
        "ok": False,
        "capability": capability,
        "output": None,
        "usage": zero_usage(),
        "error": {"type": etype, "message": message, "retryable": retryable},
    }


def zero_usage() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "cost_micros": 0}


if __name__ == "__main__":
    raise SystemExit(main())
