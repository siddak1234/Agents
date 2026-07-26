"""The subprocess transport.

The only place in the orchestrator that knows agents are processes. Swapping
in HTTP later means adding a sibling of `call()`; the envelope in
`contract.py` does not change.

Two invariants this module exists to hold:

  * The orchestrator never imports agent code. Each agent runs in its own
    interpreter with its own dependency set, so one agent's conflict cannot
    break another or the orchestrator.
  * The agent's working directory is its own folder. Everything inside an
    agent that resolves a relative path — `.env`, `alembic.ini` — depends on
    this, and getting it wrong misconfigures silently rather than loudly.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from orchestrator.contract import CallError, CallRequest, CallResult, ProtocolError
from orchestrator.manifest import AgentManifest

#: How much of a failing agent's stderr to attach. Enough to hold a
#: traceback, small enough to stay readable in a terminal.
STDERR_TAIL_LINES = 40

DEFAULT_TIMEOUT_S = 120.0


def call(
    manifest: AgentManifest,
    request: CallRequest,
    *,
    timeout_s: float | None = None,
    env: dict[str, str] | None = None,
) -> CallResult:
    """Invoke one capability. Never raises for agent-side failure.

    Anything that prevents a valid envelope from coming back becomes a
    `transport` error, so a caller has exactly one shape to handle.
    """
    budget = _budget(request, timeout_s)

    try:
        proc = subprocess.run(  # noqa: S603 — command comes from a repo-controlled manifest
            list(manifest.command),
            cwd=str(manifest.workdir),
            input=request.encode(),
            capture_output=True,
            text=True,
            timeout=budget,
            env={**os.environ, **(env or {})},
            check=False,
        )
    except FileNotFoundError:
        return _transport(
            request,
            f"cannot execute {manifest.command[0]!r} for agent {manifest.name!r} "
            f"(cwd {manifest.workdir}); is the agent installed?",
        )
    except subprocess.TimeoutExpired:
        return CallResult.failure(
            request.capability,
            CallError("timeout", f"agent {manifest.name!r} exceeded {budget:g}s", retryable=True),
        )

    if proc.returncode != 0:
        return _transport(
            request,
            f"agent {manifest.name!r} exited {proc.returncode}",
            stderr=proc.stderr,
        )

    try:
        return CallResult.decode(proc.stdout, capability=request.capability)
    except ProtocolError as exc:
        return _transport(request, f"agent {manifest.name!r}: {exc}", stderr=proc.stderr)


def describe(manifest: AgentManifest, *, timeout_s: float = 30.0) -> CallResult:
    """The handshake: prove the agent runs and its manifest matches its code."""
    from orchestrator.contract import DESCRIBE

    return call(manifest, CallRequest(capability=DESCRIBE), timeout_s=timeout_s)


def _budget(request: CallRequest, timeout_s: float | None) -> float:
    if timeout_s is not None:
        return timeout_s
    if request.deadline_ms is not None:
        # Give the agent room to return its own `timeout` error, which is far
        # more useful than a killed process, before we kill it.
        return request.deadline_ms / 1000.0 + 5.0
    return DEFAULT_TIMEOUT_S


def _transport(request: CallRequest, message: str, *, stderr: str = "") -> CallResult:
    tail = _tail(stderr)
    if tail:
        message = f"{message}\n--- agent stderr ---\n{tail}"
    return CallResult.failure(request.capability, CallError("transport", message))


def _tail(text: str) -> str:
    lines = text.strip().splitlines()
    if len(lines) <= STDERR_TAIL_LINES:
        return "\n".join(lines)
    return "\n".join(["...", *lines[-STDERR_TAIL_LINES:]])


def resolve_repo_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()
