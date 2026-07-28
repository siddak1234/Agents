---
paths:
  - "**/agent.yaml"
  - "**/agent_main.py"
  - "**/agentcall.py"
  - "_template/**"
---

# Editing an agent

Full contract in `docs/AGENT_PROTOCOL.md`. These are the parts broken by accident.

**stdout carries the envelope and nothing else.** Point `sys.stdout` at stderr
before any work, and write the envelope to the real stdout captured first.
This is usually broken by a dependency printing, not by your own code —
`realty-lead-gen` configures structlog to stdout, and one log line makes the
response unparseable.

**Exit 0 whenever an envelope was produced**, including failures. A business
failure is a successful call that returned a failure. Non-zero means "I could
not produce an envelope at all", which the orchestrator turns into a
`transport` error.

**Five error types, no more:** `invalid_request`, `unavailable`, `timeout`,
`internal`. (`transport` is orchestrator-side; agents never emit it.) A
missing credential is `unavailable` and never a crash.

**Validate your own input.** Manifest schemas are documentation — the
orchestrator does not enforce them, because two validators drift.

**`describe` must answer without heavy imports.** It runs on machines that
cannot satisfy the agent's runtime, and it gates every merge, so it must cost
nothing.

**`runtime.env.inherit` names only what a capability actually uses.** Deny by
default is the point: an agent that grades photos has no business reading a
database password because the process that launched it could.

**Code and manifest must agree.** A capability in one and not the other is a
defect. `agents describe <name>` against `--static` shows the drift.

After editing a manifest, run `uv run agents list --strict`. It is the
difference between an agent that loads and an agent that is integrated.
