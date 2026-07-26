# agentcall/v1

The contract every agent in this repository implements, and the only thing
the orchestrator knows about an agent.

## The shape

An agent is **called**, not booted. One JSON request goes in, one JSON
envelope comes out:

```
orchestrator ──spawn──▶ agent process (its own venv, its own cwd)
             ──stdin──▶ {"protocol":"agentcall/v1","capability":...,"input":{...}}
             ◀─stdout── {"protocol":"agentcall/v1","ok":true,"output":{...},"usage":{...}}
```

This mirrors the shape agents already have inside `realty-lead-gen`:
`PhotoGrader.grade(photo_urls) -> PhotoGradingResult`. Typed input,
structured result, usage attached. The protocol is that idea one level up,
across a process boundary.

## Request

Written to the agent's stdin, then stdin is closed.

```json
{
  "protocol": "agentcall/v1",
  "capability": "grade_photos",
  "input": { "photo_urls": ["https://..."], "market_hint": "Austin, TX" },
  "request_id": "01J...",
  "deadline_ms": 120000
}
```

| Field | Required | Meaning |
|---|---|---|
| `protocol` | yes | Exactly `agentcall/v1`. An agent MUST reject anything else. |
| `capability` | yes | Which operation to run. Must be declared in `agent.yaml`. |
| `input` | yes | Capability-specific object. May be `{}`. |
| `request_id` | yes | Opaque; echo it into logs for correlation. |
| `deadline_ms` | no | Advisory wall-clock budget. The orchestrator enforces it by killing the process; a well-behaved agent returns a `timeout` error before that. |

## Response

Exactly one JSON object on stdout. Nothing else, ever.

```json
{
  "protocol": "agentcall/v1",
  "ok": true,
  "capability": "grade_photos",
  "output": { "overall_condition": "C4", "rehab_total_low_cents": 1850000 },
  "usage": { "input_tokens": 4210, "output_tokens": 380, "cost_micros": 21400 },
  "error": null
}
```

On failure `ok` is `false`, `output` is `null`, and `error` is populated:

```json
{
  "protocol": "agentcall/v1",
  "ok": false,
  "capability": "grade_photos",
  "output": null,
  "usage": { "input_tokens": 0, "output_tokens": 0, "cost_micros": 0 },
  "error": { "type": "unavailable", "message": "ANTHROPIC_API_KEY is not set", "retryable": false }
}
```

`usage` is always present, zeroed when nothing was spent. Cost accounting
that is optional gets forgotten.

## Error taxonomy

Five types. Resist adding a sixth without a concrete case — a taxonomy
nobody can hold in their head gets collapsed to "internal" at the call site.

| `type` | Meaning | `retryable` |
|---|---|---|
| `invalid_request` | Unknown capability, malformed input, wrong protocol version. | `false` |
| `unavailable` | A dependency the agent needs is absent or down — missing credential, database unreachable. | `false` if config, `true` if transient |
| `timeout` | The agent gave up inside its deadline. | `true` |
| `internal` | The agent broke in a way it did not anticipate. | `false` |
| `transport` | Orchestrator-side only. The process crashed, exited non-zero, produced no envelope, or produced unparseable output. Agents never emit this. | `false` |

## Rules an agent must follow

1. **stdout carries the envelope and nothing else.** This is the rule most
   likely to be broken by accident, because it is usually broken by a
   dependency rather than by your code. `realty-lead-gen` configures
   structlog with `stream=sys.stdout`; one log line and the envelope is
   unparseable. Redirect `sys.stdout` to `sys.stderr` for the duration of
   the work and write the envelope to the real stdout you captured at
   start. Logs, warnings, and progress all go to **stderr**.

2. **Exit 0 whenever an envelope was produced** — including for `ok:false`.
   A business-level failure is a successful call that returned a failure.
   Reserve non-zero exit for "I could not produce an envelope at all"; the
   orchestrator turns that into a `transport` error and attaches your
   stderr.

3. **Never crash on a missing optional dependency.** Return `unavailable`.
   This preserves the property `realty-lead-gen` already has: absent vendor
   keys disable a capability, they do not take the process down.

4. **Import lazily.** `describe` must answer without importing your heavy
   dependencies, so discovery stays fast and works on a machine that cannot
   satisfy your runtime.

5. **Declare every capability in `agent.yaml`.** An undeclared capability
   that happens to work is not part of the contract.

## `describe`

Every agent implements one built-in capability, `describe`, taking `{}` and
returning its name and capability list. It is the handshake: it proves the
agent is installed, its entrypoint resolves, and its manifest matches its
code — without spending a cent or touching a network.

## `agent.yaml`

Lives in the agent's own folder. The orchestrator reads it; the agent does
not read anything the orchestrator owns.

```yaml
protocol: agentcall/v1
name: realty-lead-gen
description: One line, for humans and for routing.

runtime:
  type: subprocess
  command: ["uv", "run", "python", "-m", "realty_lead_gen.agentcall"]
  # cwd is always the folder containing this file. Not configurable — it is
  # what makes relative paths inside the agent (.env, alembic.ini) resolve.
  env:
    inherit: [ANTHROPIC_API_KEY, ANTHROPIC_MODEL_*]  # names or fnmatch patterns
    set: { LOG_FORMAT: json }                        # literals, never secrets

capabilities:
  - name: grade_photos
    description: Grade property photos on the Fannie Mae UAD condition scale.
    input_schema: { ... }   # JSON Schema, for documentation and routing
    output_schema: { ... }
```

Schemas in the manifest are **descriptive, not enforced by the
orchestrator**. The agent validates its own input — it is the only party
that can do so correctly, and validating in both places means two
definitions that drift. The schemas exist so a human, or a future
LLM-driven router, can see how to call the agent without reading its source.

## What the orchestrator guarantees

Three properties hold on every call, and an agent may rely on them.

**Working directory is the agent's own folder.** Not configurable. This is
what makes an agent's relative paths resolve identically whoever invoked it.
It matters more than it looks: `realty-lead-gen` reads `env_file=".env"` and
gives every setting a default, so an agent started from the wrong directory
would boot *successfully* on a development JWT secret and a localhost
database. A configurable working directory is a configurable way to get that
silently wrong.

**The environment is deny-by-default.** An agent receives `PATH`, `HOME`,
`LANG`, `LC_ALL`, `TMPDIR`, `TZ` — without which nothing can execute — plus
exactly what its manifest names in `runtime.env`. Nothing else. An agent that
grades photos has no business being able to read a database password merely
because the process that launched it could. `inherit: ["*"]` is rejected by
the manifest loader rather than merely discouraged.

Declare the minimum a capability genuinely needs. `realty-lead-gen` is the
worked example: the service holds database, Redis, MLS, portal, skip-tracing
and JWT credentials, and its manifest inherits only the Claude keys, because
that is all its capabilities use.

**Output is bounded.** stdout is captured to a temporary file, not a pipe,
and refused unread past 8 MiB. An envelope is kilobytes; anything approaching
the limit is a runaway agent, and without the ceiling it would exhaust the
orchestrator's memory rather than its own. Crossing it yields a `transport`
error naming the size. stderr is kept but only its tail is attached to
errors.

## What v1 deliberately leaves out

Each of these is a real need. None is a need *yet*, and guessing at them
with one agent in the repo would bake in the wrong answer.

- **Streaming / partial results.** One request, one response.
- **Long-running jobs.** A capability that cannot finish inside a deadline
  should return a handle in its `output` and expose a second capability to
  poll it. The protocol does not model job state.
- **Agent-to-agent calls.** Agents are leaves. Composition lives in the
  orchestrator, so the call graph stays visible in one place.
- **Auth between orchestrator and agent.** Same trust domain, same machine.
  A remote transport would need this; adding it now would be ceremony.
- **Transports other than subprocess.** The envelope is transport-agnostic
  on purpose — HTTP or a queue can carry the same JSON later without the
  contract changing. Only `runner.py` would need to grow.
