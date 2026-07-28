"""The subprocess transport.

The only place in the orchestrator that knows agents are processes. Swapping
in HTTP later means adding a sibling of `call()`; the envelope in
`contract.py` does not change.

Four invariants this module exists to hold:

  * The orchestrator never imports agent code. Each agent runs in its own
    interpreter with its own dependency set, so one agent's conflict cannot
    break another or the orchestrator.
  * The agent's working directory is its own folder. Everything inside an
    agent that resolves a relative path — `.env`, `alembic.ini` — depends on
    this, and getting it wrong misconfigures silently rather than loudly.
  * An agent sees only the environment it declared. Deny by default.
  * An agent cannot exhaust this process's memory by writing to stdout.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
import tempfile
from typing import IO

from orchestrator.contract import DESCRIBE, CallError, CallRequest, CallResult, ProtocolError
from orchestrator.manifest import AgentManifest

#: Passed to every agent regardless of what it declares — without these an
#: agent cannot locate an interpreter, a home directory, or scratch space, so
#: withholding them would not be a security posture, just a broken one. None
#: of them carry credentials.
BASE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TZ")

#: Ceiling on an agent's stdout. A JSON envelope is kilobytes; anything near
#: this is a bug, and without a ceiling a looping agent takes the orchestrator
#: down with it. Output is written to a temporary file rather than a pipe, so
#: crossing the limit costs disk we then refuse to read — never memory.
MAX_OUTPUT_BYTES = 8 * 1024 * 1024

#: How much of a failing agent's stderr to attach. Enough to hold a
#: traceback, small enough to stay readable in a terminal.
STDERR_TAIL_BYTES = 64 * 1024
STDERR_TAIL_LINES = 40

DEFAULT_TIMEOUT_S = 120.0


def build_env(
    manifest: AgentManifest,
    extra: dict[str, str] | None = None,
    *,
    inherit: bool = True,
) -> dict[str, str]:
    """The exact environment an agent will see.

    Separate from `call` so it can be tested and inspected directly — "what
    can this agent read?" should be answerable without running it.

    `inherit=False` withholds everything the manifest inherits, leaving only
    the base variables and the committed `env.set` literals. That is the
    handshake's environment: `describe` is documented as costing nothing and
    needing no credentials, and for years the code quietly disagreed — the
    handshake received every variable the manifest named, keys included, on
    every `agents check` in every CI run.
    """
    env = {key: os.environ[key] for key in BASE_ENV if key in os.environ}
    if inherit:
        env.update(
            {
                key: value
                for key, value in os.environ.items()
                if any(fnmatch.fnmatchcase(key, pattern) for pattern in manifest.env.inherit)
            }
        )
    env.update(dict(manifest.env.set))
    if extra:
        env.update(extra)
    return env


def call(
    manifest: AgentManifest,
    request: CallRequest,
    *,
    timeout_s: float | None = None,
    env: dict[str, str] | None = None,
    inherit_env: bool = True,
) -> CallResult:
    """Invoke one capability. Never raises for agent-side failure.

    Anything that prevents a valid envelope from coming back becomes a
    `transport` error, so a caller has exactly one shape to handle.
    """
    budget = _budget(request, timeout_s)
    payload = request.encode().encode("utf-8")

    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.run(  # noqa: S603 — command comes from a repo-controlled manifest
                list(manifest.command),
                cwd=str(manifest.workdir),
                input=payload,
                stdout=out,
                stderr=err,
                timeout=budget,
                env=build_env(manifest, env, inherit=inherit_env),
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
                CallError(
                    "timeout", f"agent {manifest.name!r} exceeded {budget:g}s", retryable=True
                ),
            )

        stderr = _tail(err)

        if _size(out) > MAX_OUTPUT_BYTES:
            return _transport(
                request,
                f"agent {manifest.name!r} wrote {_size(out)} bytes to stdout, over the "
                f"{MAX_OUTPUT_BYTES} byte limit; output discarded unread",
                stderr=stderr,
            )

        if proc.returncode != 0:
            return _transport(
                request, f"agent {manifest.name!r} exited {proc.returncode}", stderr=stderr
            )

        try:
            return CallResult.decode(_read(out), capability=request.capability)
        except ProtocolError as exc:
            return _transport(request, f"agent {manifest.name!r}: {exc}", stderr=stderr)


def describe(manifest: AgentManifest, *, timeout_s: float = 30.0) -> CallResult:
    """The handshake: prove the agent runs and its manifest matches its code.

    Runs with the inherited environment withheld. The handshake is documented
    everywhere as "no network, no credentials, no cost", and an agent whose
    `describe` needs a secret is an agent whose `describe` is doing work —
    which the contract forbids. Withholding it here makes the promise true
    instead of merely written down.
    """
    return call(manifest, CallRequest(capability=DESCRIBE), timeout_s=timeout_s, inherit_env=False)


def _budget(request: CallRequest, timeout_s: float | None) -> float:
    if timeout_s is not None:
        return timeout_s
    if request.deadline_ms is not None:
        # Give the agent room to return its own `timeout` error, which is far
        # more useful than a killed process, before we kill it.
        return request.deadline_ms / 1000.0 + 5.0
    return DEFAULT_TIMEOUT_S


def _size(fh: IO[bytes]) -> int:
    fh.seek(0, os.SEEK_END)
    return fh.tell()


def _read(fh: IO[bytes]) -> str:
    fh.seek(0)
    return fh.read().decode("utf-8", errors="replace")


def _tail(fh: IO[bytes]) -> str:
    size = _size(fh)
    fh.seek(max(0, size - STDERR_TAIL_BYTES))
    text = fh.read().decode("utf-8", errors="replace").strip()
    lines = text.splitlines()
    if len(lines) <= STDERR_TAIL_LINES:
        return "\n".join(lines)
    return "\n".join(["...", *lines[-STDERR_TAIL_LINES:]])


def _transport(request: CallRequest, message: str, *, stderr: str = "") -> CallResult:
    if stderr:
        message = f"{message}\n--- agent stderr ---\n{stderr}"
    return CallResult.failure(request.capability, CallError("transport", message))
