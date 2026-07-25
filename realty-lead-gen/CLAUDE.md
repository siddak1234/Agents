# realty-lead-gen

Real-estate lead-generation backend. Ingests listings from MLS, portals, and
off-market sources; enriches each property with Claude-powered condition
grading, valuation, and comps; scores per persona (flipper / wholesaler /
buyer's agent); materializes ranked leads into Postgres.

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
make setup            # uv sync + pre-commit hooks
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
- **`mypy` runs strict and Ruff has the bandit (`S`) rules on.** Both are
  enforced by pre-commit; do not weaken a rule to make a commit pass.

## Adapters degrade, they do not fail

The API and worker boot with no vendor API keys. Source adapters disable
themselves when their credentials are absent, so the pipeline degrades
gracefully in dev and demo. Preserve that property: a missing key is a
disabled adapter, never a crash. `.env.example` lists every key.

## CI is currently dormant

`.github/workflows/ci.yml` here is a complete pipeline — lint, typecheck,
unit tests, integration against real Postgres and Redis, and a combined
coverage gate. GitHub Actions only discovers workflows at the *repository*
root, so nested one level down it does not run.

It is intentionally left unmodified so this folder stays self-contained.
Activating it means a root-level `.github/workflows/realty-lead-gen-ci.yml`
with a `paths:` filter on `realty-lead-gen/**` and the working directory,
uv cache glob, and artifact paths adjusted for the nested location.
