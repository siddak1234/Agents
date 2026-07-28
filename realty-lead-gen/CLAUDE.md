# realty-lead-gen

Real-estate lead-generation backend. Built to ingest listings from MLS,
portals, and off-market sources; enrich each property with Claude-powered
condition grading, valuation, and comps; score per persona (flipper /
wholesaler / buyer's agent); and materialize ranked leads into Postgres.
Which of those paths are live versus stubbed is exactly what
`ARCHITECTURE.md`'s status table records — several source adapters are
stubs, and that table, not this paragraph, is the claim to trust.

Stack: Python 3.13, FastAPI + async SQLAlchemy, Postgres 17 with PostGIS,
Redis with arq for background jobs, Anthropic SDK for vision and reasoning.
Dependencies managed with `uv`.

`ARCHITECTURE.md` is the source of truth for design decisions and explains
the reasoning behind every rule below. Read it before changing anything
structural — it documents what was considered and rejected, not just what
was chosen.

## Commands

Run these from this folder, not the repository root.

```bash
make setup            # uv sync (never `pre-commit install` — see the Makefile)
make hooks            # this folder's pre-commit hooks, run on demand
make test             # unit tests — fast, no docker
make test-integration # needs docker (spins up postgres + redis)
make test-cov         # full suite with coverage
make lint             # ruff + mypy strict
make fmt              # ruff format
make migrate          # alembic upgrade head
make api              # FastAPI on :8000
make worker           # arq worker
```

## House rules

These are easy to violate by accident and expensive to unwind:

- **Money is integer cents.** Never a float, never a bare Decimal on the
  wire. See `utils/money.py`.
- **Timestamps are timezone-aware UTC**, always — including in tests.
- **External I/O goes through a Protocol.** Adapters in `sources/` and
  `enrichment/` implement a Protocol so they can be mocked; business logic
  never calls a vendor SDK directly.
- **Mutable state is append-only.** Property attributes, enrichment runs,
  deal analyses, and scores are snapshot tables, not in-place updates. The
  audit trail is a design goal — "why did we see it this way at time T?"
  must stay answerable.
- **`mypy` runs strict and Ruff has the bandit (`S`) rules on.** Ruff runs
  in this folder's hooks (`make hooks`) and both run in CI; do not weaken a
  rule to make a commit pass.

## Adapters degrade, they do not fail

The API and worker boot with no vendor API keys. Source adapters disable
themselves when their credentials are absent, so the pipeline degrades
gracefully in dev and demo. Preserve that property: a missing key is a
disabled adapter, never a crash. `.env.example` lists every key.

## This agent's orchestrator entrypoint

`src/realty_lead_gen/agentcall.py` implements `agentcall/v1` (see
`AGENT_PROTOCOL.md` at the repository root) and is what
`uv run agents call realty-lead-gen ...` invokes. It is an **adapter**: it
translates the wire protocol into calls on `realty_lead_gen.agents` and back.
No business logic belongs there.

Its capability list mirrors `agent.yaml` by hand. When you add a capability,
change both, then run `uv run agents check` from the repository root.

The two traps it exists to avoid, both easy to reintroduce:

- `configure_logging` sends structlog to **stdout** (`logging.py`). The
  adapter repoints `sys.stdout` at stderr before anything runs, because one
  log line on stdout makes the response envelope unparseable.
- `describe` must answer without importing `anthropic`, SQLAlchemy, or
  settings, so keep those imports inside the capability that needs them.

## This agent's CI lives at the repository root

`.github/workflows/realty-lead-gen.yml`, scoped with a `paths:` filter on
`realty-lead-gen/**`. There is no workflow inside this folder — GitHub
Actions only discovers workflows at the *repository* root, so one here would
never run, and a pipeline that silently never runs is worse than none.

It is this agent's own pipeline, unchanged in substance: ruff, strict mypy,
unit tests, integration against real Postgres and Redis, and a combined
coverage gate reading `fail_under` from this folder's `pyproject.toml`.

The workflow sets `defaults.run.working-directory: realty-lead-gen`, so
`run:` steps behave as if launched from here. Two things do **not** follow
that setting and stay root-prefixed — artifact `path:` values and the uv
`cache-dependency-glob`. Both are silent failures if you forget them when
editing.
