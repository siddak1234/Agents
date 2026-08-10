#!/usr/bin/env python3
"""`agentcall/v1` adapter for the Operations Maintenance Agent.

This file translates the wire protocol into calls on `planner` and back. No
business logic belongs here — the planning itself, its validation and its
error vocabulary live in `planner.py`.

The contract lives in docs/AGENT_PROTOCOL.md at the repository root. Run it
without the orchestrator at all:

    echo '{"protocol":"agentcall/v1","capability":"describe","input":{}}' \\
      | uv run python agent_main.py

`describe` deliberately imports nothing heavy, so that command answers on a
machine that cannot install this agent's dependencies.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

PROTOCOL = "agentcall/v1"


AGENT_NAME = "operations-maintenance-agent"


# Wording is the manifest's, verbatim. A caller reading agent.yaml and a
# caller invoking `describe` must not be told two different things — nothing
# in the platform compares these two copies, so keeping them identical is this
# file's job.
CAPABILITIES = (
    ("describe", "Report this agent's name and capabilities."),
    (
        "generate_maintenance_plan",
        "Generate an optimized refinery maintenance plan from the provided "
        "operational constraints.",
    ),
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
        # Imported inside the branch that needs it: `describe` must not pay for
        # the Anthropic SDK, and must still answer where it is not installed.
        from planner import (  # noqa: PLC0415 — lazy import per rule 6, keeps describe cheap
            InvalidInputError,
            MissingCredentialError,
            ModelOutputError,
            TransientModelError,
            generate_maintenance_plan,
        )

        # RULE 4: validate your own input and say precisely what was wrong. The
        # orchestrator does not validate for you — the schema in agent.yaml is
        # documentation, and two validators would drift. `planner` does the
        # checking; the four exception types below are how it reports which
        # side of the call went wrong, and whether trying again could help.
        try:
            response = generate_maintenance_plan(payload)
        except InvalidInputError as exc:
            # The caller got it wrong.
            return fail(capability, "invalid_request", str(exc))
        except MissingCredentialError as exc:
            # RULE 3: a missing credential is a disabled capability, not a
            # crash — and never a substring match on someone else's message.
            return fail(capability, "unavailable", str(exc))
        except TransientModelError as exc:
            # `planner` already sorted connection trouble, timeouts, rate
            # limits and retryable 5xx from permanent failures, and the SDK's
            # own retry budget (MAX_RETRIES) is already spent by the time this
            # is raised. Reporting it `internal`/`retryable: false` — the
            # generic handler below — told a caller a retry could not
            # possibly help, in the one case where it can.
            traceback.print_exc(file=sys.stderr)
            timed_out = isinstance(exc.__cause__, TimeoutError)
            return fail(
                capability,
                "timeout" if timed_out else "unavailable",
                f"{type(exc).__name__}: {exc}",
                retryable=True,
            )
        except ModelOutputError as exc:
            # The caller's request was fine and the model's answer was not.
            # `internal` with the type's documented `retryable: false`: the
            # call forces `tool_choice`, so a missing or malformed plan is the
            # API not keeping its own contract — which is what "broke in a way
            # it did not anticipate" means, and it is where realty-lead-gen
            # sends the same failure. What is *not* zeroed is the usage: those
            # tokens were billed before the answer could be inspected.
            return fail(capability, "internal", str(exc), usage=exc.usage)
        except Exception as exc:  # noqa: BLE001 — an envelope is mandatory
            traceback.print_exc(file=sys.stderr)
            return fail(capability, "internal", f"planning failed: {exc}")

        return ok(capability, response["plan"], usage=response["usage"])

    declared = ", ".join(n for n, _ in CAPABILITIES)
    return fail(capability, "invalid_request", f"unknown capability; this agent offers: {declared}")


# The five error types are: invalid_request, unavailable, timeout, internal,
# transport (transport is orchestrator-side; agents never emit it).


def ok(
    capability: str, output: dict[str, Any], usage: dict[str, int] | None = None
) -> dict[str, Any]:
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


def fail(
    capability: str,
    etype: str,
    message: str,
    *,
    retryable: bool = False,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    # `usage` is a parameter because a failure can still have cost money: a
    # model call that returns an unusable answer is billed for it, and zeroing
    # that would make real spend invisible to whoever is counting.
    return {
        "protocol": PROTOCOL,
        "ok": False,
        "capability": capability,
        "output": None,
        "usage": usage or zero_usage(),
        "error": {"type": etype, "message": message, "retryable": retryable},
    }


def zero_usage() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "model": None}


if __name__ == "__main__":
    raise SystemExit(main())
