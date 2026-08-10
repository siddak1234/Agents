#!/usr/bin/env python3
"""agentcall/v1 adapter for the phishing-incident-triage agent.

Reads one JSON request from stdin, writes exactly one JSON envelope to
stdout, and exits.  This module owns no business logic: it translates
the wire protocol into calls on ``phishing_triage`` and translates
results back.

Rules enforced:
  RULE 1 — stdout carries the envelope and nothing else.
  RULE 2 — exit 0 whenever an envelope was produced.
  RULE 3 — missing credentials return ``unavailable``, never a crash.
  RULE 4 — validate own input and say what was wrong.
  RULE 5 — declare only environment actually used (none needed).
  RULE 6 — ``describe`` is cheap; heavy imports stay inside the handler.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

PROTOCOL = "agentcall/v1"
AGENT_NAME = "phishing-incident-triage"

# Mirrors agent.yaml by hand. `agents check` fails if the two disagree.
CAPABILITIES = (
    ("describe", "Report this agent's name and capabilities. Costs nothing."),
    (
        "triage_email",
        "Analyse a suspicious email and return a structured phishing-risk "
        "assessment with severity, indicators, and recommended actions.",
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
    # RULE 2: exit 0 whenever an envelope was produced.
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

    if capability == "triage_email":
        return _triage_email(capability, payload)

    declared = ", ".join(n for n, _ in CAPABILITIES)
    return fail(capability, "invalid_request", f"unknown capability; this agent offers: {declared}")


# ---------------------------------------------------------------------------
# Capability handlers
# ---------------------------------------------------------------------------


def _triage_email(cap: str, payload: dict[str, Any]) -> dict[str, Any]:
    # RULE 4: validate every field, say precisely what was wrong.
    subject = payload.get("subject")
    if not isinstance(subject, str) or not subject.strip():
        return fail(cap, "invalid_request", "'subject' must be a non-empty string")

    sender = payload.get("sender")
    if not isinstance(sender, str) or not sender.strip():
        return fail(cap, "invalid_request", "'sender' must be a non-empty string")

    reply_to = payload.get("reply_to")
    if reply_to is not None and (not isinstance(reply_to, str) or not reply_to.strip()):
        return fail(cap, "invalid_request", "'reply_to' must be a non-empty string when present")

    body = payload.get("body")
    if body is None:
        body = ""
    if not isinstance(body, str):
        return fail(cap, "invalid_request", "'body' must be a string")

    urls = payload.get("urls")
    if urls is None:
        urls = []
    if not isinstance(urls, list) or not all(isinstance(u, str) for u in urls):
        return fail(cap, "invalid_request", "'urls' must be a list of strings")

    attachments = payload.get("attachments")
    if attachments is None:
        attachments = []
    if not isinstance(attachments, list) or not all(isinstance(a, str) for a in attachments):
        return fail(cap, "invalid_request", "'attachments' must be a list of strings")

    spf = payload.get("spf")
    if spf is not None and not isinstance(spf, str):
        return fail(cap, "invalid_request", "'spf' must be a string when present")

    dkim = payload.get("dkim")
    if dkim is not None and not isinstance(dkim, str):
        return fail(cap, "invalid_request", "'dkim' must be a string when present")

    dmarc = payload.get("dmarc")
    if dmarc is not None and not isinstance(dmarc, str):
        return fail(cap, "invalid_request", "'dmarc' must be a string when present")

    # Imported here, not at module load, so `describe` works without them.
    # RULE 6: heavy imports stay inside the handler.
    from phishing_triage import triage_email as _triage

    try:
        result = _triage(
            subject=subject.strip(),
            sender=sender.strip(),
            reply_to=reply_to.strip() if reply_to else None,
            body=body,
            urls=urls,
            attachments=attachments,
            spf=spf.strip() if spf else None,
            dkim=dkim.strip() if dkim else None,
            dmarc=dmarc.strip() if dmarc else None,
        )
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        return fail(cap, "internal", f"{type(exc).__name__}: {exc}")

    return ok(cap, result)


# ---------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------


def ok(capability: str, output: dict[str, Any], usage: dict[str, int] | None = None):
    return {
        "protocol": PROTOCOL,
        "ok": True,
        "capability": capability,
        "output": output,
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
    # RULE 5: no env vars needed; usage is zeroed.
    return {"input_tokens": 0, "output_tokens": 0, "model": None}


if __name__ == "__main__":
    raise SystemExit(main())
