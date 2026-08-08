# agentcall/v1

The contract every agent implements, and the only thing the orchestrator
knows about an agent.

An agent is **called**, not booted. One JSON request in, one JSON envelope
out:

```
orchestrator ──spawn──▶ agent process (its own venv, its own cwd)
             ──stdin──▶ {"protocol":"agentcall/v1","capability":…,"input":{…}}
             ◀─stdout── {"protocol":"agentcall/v1","ok":true,"output":{…},"usage":{…}}
```

This is the shape agents already have inside `realty-lead-gen` —
`PhotoGrader.grade(urls) -> PhotoGradingResult`, typed input, structured
result, usage attached — lifted one level up across a process boundary.

## Request

Written to stdin, which is then closed.

```json
{
  "protocol": "agentcall/v1",
  "capability": "grade_photos",
  "input": { "photo_urls": ["https://…"], "market_hint": "Austin, TX" },
  "request_id": "01J…",
  "deadline_ms": 120000
}
```

| Field | Required | Meaning |
|---|---|---|
| `protocol` | yes | Exactly `agentcall/v1`. Reject anything else. |
| `capability` | yes | Which operation to run. Must be declared in `agent.yaml`. |
| `input` | yes | Capability-specific object. May be `{}`. |
| `request_id` | yes | Always present; opaque. Echo it into your logs for correlation. The orchestrator sends `""` unless a caller supplies one, so treat empty as normal. |
| `deadline_ms` | no | Advisory budget. The orchestrator enforces it by killing the process — return a `timeout` error before that. |

## Response

Exactly one JSON object on stdout. Nothing else, ever.

```json
{
  "protocol": "agentcall/v1",
  "ok": true,
  "capability": "grade_photos",
  "output": { "overall_condition": "C4", "rehab_total_low_cents": 1850000 },
  "usage": { "input_tokens": 4210, "output_tokens": 380, "model": "claude-sonnet-4-5" },
  "error": null
}
```

On failure, `ok` is `false`, `output` is `null`, and `error` is populated.
`usage` is always present, zeroed when nothing was spent — accounting that is
optional gets forgotten.

`usage` reports what the agent **observed**: tokens it was told it used, and
the model it called (`null` when it called none). It does not report money.

That is a correction, not an omission. `usage` carried a `cost_micros` field
until an audit found three agents each maintaining a private copy of a vendor
price list to populate it — one of them wrong in three rows, priced from a
model generation two releases old, overstating Opus threefold and
understating Haiku fourfold. Nothing consumed the number, so nothing noticed.
A price is a vendor fact that changes without notice; a token count is a fact
the agent watched happen. Copying the first into every agent means N copies
ageing independently, and the field they feed had no reader.

So cost is derived, once, wherever someone actually needs it — from `model`
and the token counts, against a single table owned there. Until something
needs it, no table exists to go stale. An agent that wants its spend visible
reports the model honestly and stops.

**What existing agents must do, and when.** Replace `cost_micros` with
`model` in every `usage` object you emit, and delete the price table it was
computed from. Do it now: `Usage.from_wire` no longer reads `cost_micros`, so
an agent still emitting it reports `model: null` — it decodes, and silently
claims no model was called. `model` is a string when the capability called
one and `null` when it did not; a non-string is a protocol error. All five
agents in this repository were migrated in the commit that changed this
file.

| `error.type` | Meaning | `retryable` |
|---|---|---|
| `invalid_request` | Unknown capability, malformed input, wrong protocol. | `false` |
| `unavailable` | A needed dependency is absent or down — missing credential, unreachable database. | `false` if config, `true` if transient |
| `timeout` | The agent gave up inside its deadline. | `true` |
| `internal` | The agent broke in a way it did not anticipate. | `false` |
| `transport` | Orchestrator-side only: crashed, exited non-zero, produced no envelope or an unparseable one. Agents never emit this. | `false` |

Five types. Resist a sixth without a concrete case — a taxonomy nobody can
hold in their head collapses to `internal` at the call site.

## Rules an agent must follow

1. **stdout carries the envelope and nothing else.** Usually broken by a
   dependency rather than your own code: `realty-lead-gen` configures
   structlog with `stream=sys.stdout`, and one log line makes the envelope
   unparseable. Point `sys.stdout` at stderr before doing any work, and write
   the envelope to the real stdout you captured first.
2. **Exit 0 whenever an envelope was produced**, including for `ok:false`. A
   business failure is a successful call that returned a failure. Non-zero
   means "I could not produce an envelope at all", which the orchestrator
   turns into a `transport` error with your stderr attached.
3. **Never crash on a missing optional dependency.** Return `unavailable`, so
   an absent vendor key disables a capability instead of killing the process.
4. **Validate your own input.** Manifest schemas are documentation; the
   orchestrator does not enforce them, because two validators drift.
5. **Import lazily.** `describe` must answer without your heavy dependencies.
6. **Declare every capability in `agent.yaml`.** One that works but is not
   declared is not part of the contract.

## `describe`

Every agent implements it. Takes `{}`, returns its name and capability list.
It is the handshake: it proves the agent is installed, its entrypoint
resolves, and its manifest matches its code — without spending a cent or
touching a network. `agents check` calls it on every registered agent.

## `agent.yaml`

Lives in the agent's folder. The orchestrator reads it; agents read nothing
the orchestrator owns.

```yaml
protocol: agentcall/v1
name: realty-lead-gen                    # must equal the folder name
description: One line, for humans and for routing.

runtime:
  type: subprocess
  command: ["uv", "run", "python", "-m", "realty_lead_gen.agentcall"]
  # `--extra dev` matters: CI syncs an agent's default dependencies only, so
  # a test or lint command must install its own dev tools or it fails in CI
  # and nowhere else. These two lines mirror the real manifest exactly.
  test: ["uv", "run", "--frozen", "--extra", "dev", "pytest", "-m", "unit", "-q"]
  lint: ["uv", "run", "--frozen", "--extra", "dev", "make", "lint"]
  env:
    inherit: [ANTHROPIC_API_KEY, ANTHROPIC_MODEL_*]   # exact names, or prefix + `*`
    set: { LOG_FORMAT: json }                         # literals, never secrets

capabilities:
  # Mandatory. `manifest.py` refuses to load an agent without it, so an
  # example that omitted it would not be a working example.
  - name: describe
    description: Report this agent's name and capabilities. Costs nothing.
    input_schema: { type: object, properties: {} }
    output_schema: { type: object, required: [name, protocol, capabilities] }

  - name: grade_photos
    description: Grade property photos on the Fannie Mae UAD condition scale.
    input_schema: { … }     # JSON Schema — documentation and routing, not enforcement
    output_schema: { … }
```

## What the orchestrator guarantees

**Working directory is the agent's own folder.** Not configurable. It is why
an agent's relative paths resolve identically whoever invoked it —
`realty-lead-gen` reads `env_file=".env"` and defaults every setting, so an
agent started from the wrong directory would come up *successfully* with
every credential unset rather than failing loudly. It is also how `uv run`
finds the right `pyproject.toml`.

**The environment is deny-by-default.** An agent gets `PATH`, `HOME`, `LANG`,
`LC_ALL`, `TMPDIR`, `TZ` — without which nothing executes, none carrying
credentials — plus exactly what `runtime.env` names. The `describe` handshake
is stricter still: it runs with the inherited variables withheld entirely, so
"costs nothing, needs no credentials" is enforced rather than promised. An agent that grades
photos has no business reading a database password because the process that
launched it could.

An `inherit` entry is either an **exact variable name** or a **literal prefix
of at least three characters followed by one trailing `*`**. Anything else is
rejected by the loader. The rule is a shape rule rather than a ban on `"*"`
because matching is done with `fnmatch`, which also reads `?` and `[…]`:
`"*"`, `?*`, `**`, `*_*` and `[A-Z]*` all mean "the whole environment", and a
one- or two-character prefix is the same thing written quietly.

**That rule is not a safety guarantee.** It rejects entries that match
approximately everything; it cannot tell whether a well-formed prefix is too
broad for *this* agent. `AWS*` is legal here and reaches
`AWS_SECRET_ACCESS_KEY`; so do `DB_*`, `API*` and `JWT*`. Whether a capability
needs the family it names is a review question, and no character count settles
it — see docs/CONTRIBUTING.md, where `runtime.env.inherit` broader than the
capabilities justify is a blocking finding.

**Output is bounded.** stdout goes to a temporary file, not a pipe, and is
refused unread past 8 MiB. An envelope is kilobytes; anything near the limit
is a runaway agent, and without the ceiling it would exhaust the
orchestrator's memory rather than its own.

## What v1 leaves out

Each is a real need, none is a need *yet*, and guessing with one agent in the
repo would bake in the wrong answer.

- **Streaming and partial results.** One request, one response.
- **Long-running jobs.** A capability that cannot finish inside a deadline
  should return a handle and expose a second capability to poll it. The
  protocol does not model job state.
- **Agent-to-agent calls.** Agents are leaves; composition belongs in the
  orchestrator so the call graph stays visible in one place.
- **Auth between orchestrator and agent.** Same trust domain, same machine.
- **Transports other than subprocess.** The envelope is transport-agnostic;
  HTTP or a queue would touch only `runner.py`.
