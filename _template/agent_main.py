#!/usr/bin/env python3
"""A minimal agentcall/v1 agent. Copy this folder to start a new agent.

Standard library only, on purpose: you can run it right now, and it shows the
whole contract in one screen. A real agent brings its own dependencies (see
`realty-lead-gen`), but nothing here requires them.

Try it without the orchestrator at all:

    echo '{"protocol":"agentcall/v1","capability":"describe","input":{}}' \\
      | python3 agent_main.py

The contract lives in AGENT_PROTOCOL.md at the repository root. The four
rules that matter are marked RULE below.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

PROTOCOL = "agentcall/v1"

# TODO(new agent): rename this to match your folder name. Discovery rejects a
# manifest whose `name` differs from the folder it sits in.
AGENT_NAME = "_template"

# TODO(new agent): declare your capabilities here AND in agent.yaml. The two
# are kept in sync by hand; `agents describe <name>` against
# `agents describe <name> --static` shows any drift.
CAPABILITIES = (
    ("describe", "Report this agent's name and capabilities. Costs nothing."),
    ("greet", "Return a greeting. Replace this with something useful."),
)


def main() -> int:
    # RULE 1: stdout carries the envelope and nothing else. Point sys.stdout at
    # stderr before doing anything, so a stray print() — yours or a library's —
    # cannot corrupt the response. Write the envelope to the real stdout saved
    # here. This is the rule most often broken by accident.
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

    # RULE 2: exit 0 whenever an envelope was produced, including for failures.
    # A business failure is a successful call that returned a failure. Non-zero
    # means "I could not produce an envelope at all", and the orchestrator
    # turns that into a `transport` error.
    return 0


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
        return ok(
            capability,
            {
                "name": AGENT_NAME,
                "protocol": PROTOCOL,
                "capabilities": [{"name": n, "description": d} for n, d in CAPABILITIES],
            },
        )

    if capability == "greet":
        # RULE 3: validate your own input and say precisely what was wrong. The
        # orchestrator does not validate for you — the schema in agent.yaml is
        # documentation, and two validators would drift.
        who = payload.get("name", "world")
        if not isinstance(who, str) or not who.strip():
            return fail(capability, "invalid_request", "'name' must be a non-empty string")
        return ok(capability, {"greeting": f"Hello, {who.strip()}!"})

    declared = ", ".join(n for n, _ in CAPABILITIES)
    return fail(
        capability, "invalid_request", f"unknown capability; this agent offers: {declared}"
    )


# RULE 4: a missing credential or unreachable dependency returns
# `unavailable`, never a crash. See realty-lead-gen's agentcall.py for a real
# example — no ANTHROPIC_API_KEY disables photo grading instead of killing the
# process. The five error types are: invalid_request, unavailable, timeout,
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
