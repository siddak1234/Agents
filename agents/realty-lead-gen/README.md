# realty-lead-gen

Grades property condition from listing photos on the Fannie Mae UAD scale
(C1–C6) and estimates a rehab range, in one Claude vision call. Called over
`agentcall/v1`; see `docs/AGENT_PROTOCOL.md` at the repository root.

This folder used to hold an entire lead-generation service — FastAPI,
Postgres, migrations, source adapters, background jobs. That service now
lives in its own repository (`realty-lead-gen-service`); what remains here
is the agent: the eleven modules reachable from the agentcall entrypoint,
and the tests that cover them. The split is the repository's shape rule
applied to its own reference agent.

## Quickstart

```bash
make setup                # uv sync --all-extras
cp .env.example .env      # optional; every variable is optional
```

Call it from the repository root:

```bash
uv run agents describe realty-lead-gen
uv run agents call realty-lead-gen grade_photos \
  --input '{"photo_urls": ["https://example.com/kitchen.jpg"], "market_hint": "Austin, TX"}'
```

Without `ANTHROPIC_API_KEY`, `describe` still answers and `grade_photos`
returns a structured `unavailable` error — a missing key is a disabled
capability, never a crash.

## Commands

| Command | What it does |
|---|---|
| `make setup` | create the venv, install all extras |
| `make test` | unit tests (`pytest -m unit`) |
| `make lint` | ruff + strict mypy |
| `make fmt` | format |
| `make hooks` | this folder's pre-commit hooks, run on demand |

The orchestrator runs `make lint` and the unit suite via the agent's own
declared `runtime.lint` / `runtime.test` — the same commands, from this
folder, which is also what CI does.

## Capabilities

| Capability | In | Out |
|---|---|---|
| `describe` | `{}` | name, protocol, capability list |
| `grade_photos` | `photo_urls` (list of URLs), optional `market_hint` | UAD condition grade, confidence, rehab range in integer cents, per-system findings, red flags |

Schemas in full in [`agent.yaml`](./agent.yaml). Money is integer cents —
never floats on the wire.

## Configuration

Every variable is optional; [`.env.example`](./.env.example) lists all of
them. `ANTHROPIC_API_KEY` enables grading; `ANTHROPIC_MODEL_VISION` selects
the model; `APP_LOG_LEVEL` / `APP_LOG_FORMAT` shape the stderr logs.

## Design notes

- **stdout carries the envelope and nothing else.** The entrypoint rebinds
  `sys.stdout` to stderr before anything else runs and keeps the real stdout
  aside, using it only to write the envelope. Everything that prints —
  structlog, a stray `print`, a chatty dependency — therefore lands on
  stderr. One stray line on stdout is a broken response; this is the trap
  the adapter exists to avoid.
- **`describe` imports nothing heavy.** The `anthropic` import lives inside
  the grading path, so the handshake answers on a machine that cannot
  install the agent's dependencies.
- **Structured output through a tool schema.** The grader forces Claude's
  answer through a JSON schema (pinned by a golden-file test in
  `tests/golden/`), so the model returns data, not prose to parse.
