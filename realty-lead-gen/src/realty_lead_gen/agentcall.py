"""agentcall/v1 adapter — how the repository orchestrator calls this agent.

Read one JSON request from stdin, write one JSON envelope to stdout. See
`AGENT_PROTOCOL.md` at the repository root for the contract.

This module is an adapter and nothing more. It owns no business logic: it
translates the wire protocol into calls on the agents that already live in
`realty_lead_gen.agents` and translates their results back. If you find
yourself computing something here, it belongs one layer down.

Two things it must get right:

* **stdout carries the envelope and nothing else.** This service configures
  structlog with ``stream=sys.stdout`` (see ``logging.py``), so a single log
  line would make the envelope unparseable. ``main`` points ``sys.stdout``
  at stderr before importing or running anything, and writes the envelope to
  the real stdout it captured first. That guards against any dependency
  printing, not just our own logging.

* **Imports stay lazy.** ``describe`` must answer on a machine that cannot
  satisfy this agent's runtime, so nothing heavy is imported at module load.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Final

PROTOCOL: Final = "agentcall/v1"
AGENT_NAME: Final = "realty-lead-gen"

#: Mirrors `agent.yaml`. The two are kept in sync by hand; comparing
#: `agents describe realty-lead-gen` against `--static` shows any drift.
CAPABILITIES: Final = (
    ("describe", "Report this agent's name and capabilities. Costs nothing."),
    (
        "grade_photos",
        "Grade property photos on the Fannie Mae UAD 3.6 condition scale and "
        "estimate a rehab range.",
    ),
)


def main() -> int:
    """Always returns 0 when an envelope was produced, including failures."""
    real_stdout = sys.stdout
    sys.stdout = sys.stderr  # see module docstring — do this before anything else

    try:
        envelope = _dispatch(sys.stdin.read())
    except Exception as exc:  # noqa: BLE001 — last resort; an envelope is mandatory
        traceback.print_exc(file=sys.stderr)
        envelope = _fail("", "internal", f"{type(exc).__name__}: {exc}")
    finally:
        sys.stdout = real_stdout

    json.dump(envelope, real_stdout)
    real_stdout.write("\n")
    real_stdout.flush()
    return 0


def _dispatch(raw: str) -> dict[str, Any]:
    try:
        request = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        return _fail("", "invalid_request", f"stdin is not valid JSON: {exc}")
    if not isinstance(request, dict):
        return _fail("", "invalid_request", "request must be a JSON object")

    if request.get("protocol") != PROTOCOL:
        return _fail(
            "", "invalid_request", f"unsupported protocol {request.get('protocol')!r}"
        )

    capability = request.get("capability")
    if not isinstance(capability, str):
        return _fail("", "invalid_request", "missing 'capability'")

    payload = request.get("input")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return _fail(capability, "invalid_request", "'input' must be an object")

    if capability == "describe":
        return _ok(capability, _describe())
    if capability == "grade_photos":
        return _grade_photos(payload)

    declared = ", ".join(name for name, _ in CAPABILITIES)
    return _fail(
        capability, "invalid_request", f"unknown capability; this agent offers: {declared}"
    )


def _describe() -> dict[str, Any]:
    return {
        "name": AGENT_NAME,
        "protocol": PROTOCOL,
        "capabilities": [{"name": n, "description": d} for n, d in CAPABILITIES],
    }


def _grade_photos(payload: dict[str, Any]) -> dict[str, Any]:
    cap = "grade_photos"

    urls = payload.get("photo_urls")
    if not isinstance(urls, list) or not urls or not all(isinstance(u, str) and u for u in urls):
        return _fail(cap, "invalid_request", "'photo_urls' must be a non-empty list of strings")
    market_hint = payload.get("market_hint")
    if market_hint is not None and not isinstance(market_hint, str):
        return _fail(cap, "invalid_request", "'market_hint' must be a string when present")

    # Imported here, not at module load, so `describe` works without them.
    import asyncio

    from realty_lead_gen.agents.claude_client import ClaudeClient
    from realty_lead_gen.agents.photo_grader import PhotoGrader
    from realty_lead_gen.config import get_settings

    settings = get_settings()
    claude = ClaudeClient(settings)
    if not claude.available:
        # The house rule: a missing credential disables a capability, it does
        # not crash the process. `unavailable` is not retryable because
        # retrying will not conjure a key.
        return _fail(
            cap,
            "unavailable",
            "ANTHROPIC_API_KEY is not set; photo grading is disabled. "
            "Set it in realty-lead-gen/.env",
            retryable=False,
        )

    try:
        result = asyncio.run(PhotoGrader(claude, settings).grade(urls, market_hint=market_hint))
    except Exception as exc:  # noqa: BLE001 — surface as a typed error, not a crash
        traceback.print_exc(file=sys.stderr)
        return _fail(cap, "internal", f"{type(exc).__name__}: {exc}")

    usage = result.usage
    return _ok(
        cap,
        {
            "overall_condition": result.overall_condition,
            "overall_confidence": result.overall_confidence,
            "rehab_total_low_cents": result.rehab_total_low_cents,
            "rehab_total_high_cents": result.rehab_total_high_cents,
            "systems": result.systems,
            "red_flags": result.red_flags,
            "notes_for_reviewer": result.notes_for_reviewer,
            "model": usage.model,
        },
        usage={
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            # The envelope's unit is micros of USD, which is what LlmUsage
            # already tracks; the field is renamed, not converted.
            "cost_micros": usage.cost_usd_micros,
        },
    )


def _ok(
    capability: str, output: dict[str, Any], usage: dict[str, int] | None = None
) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "ok": True,
        "capability": capability,
        "output": output,
        "usage": usage or _zero_usage(),
        "error": None,
    }


def _fail(
    capability: str, etype: str, message: str, *, retryable: bool = False
) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "ok": False,
        "capability": capability,
        "output": None,
        "usage": _zero_usage(),
        "error": {"type": etype, "message": message, "retryable": retryable},
    }


def _zero_usage() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "cost_micros": 0}


if __name__ == "__main__":
    raise SystemExit(main())
