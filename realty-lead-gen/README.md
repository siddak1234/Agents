# realty-lead-gen

Agentic real-estate lead-generation backend. Standalone service that
ingests listings from MLS + portals + off-market sources, enriches
each property with Claude-powered condition grading, valuation, and
comps, scores properties per persona (flipper / wholesaler / buyer's
agent), and materializes ranked leads into Postgres for a frontend to
consume.

Details in [`ARCHITECTURE.md`](./ARCHITECTURE.md). See that document
before shipping — it captures every design decision and the research
that drove it.

## Quickstart

```bash
make setup       # uv sync + install pre-commit
cp .env.example .env
make dev-up      # postgres + redis in docker
make migrate     # apply Alembic migrations
make api         # FastAPI on :8000
# in another terminal:
make worker      # arq background worker
```

Docs: <http://localhost:8000/docs>

## Commands

| Command | What it does |
|---|---|
| `make setup` | Install all deps + pre-commit hooks |
| `make fmt` | Format code (ruff) |
| `make lint` | Static checks (ruff + mypy strict) |
| `make test` | Fast unit tests |
| `make test-integration` | Full tests w/ Postgres+Redis via testcontainers |
| `make test-cov` | Full suite with coverage |
| `make migrate` | `alembic upgrade head` |
| `make migration-new msg="..."` | Autogenerate a new migration |
| `make api` | Run FastAPI locally |
| `make worker` | Run arq worker locally |
| `make dev-up` / `make dev-down` | Start / stop dependency containers |
| `make docker-build` | Build the production image |

## Layout

```
src/realty_lead_gen/
├── config.py           settings (pydantic-settings)
├── db.py               async SQLAlchemy engine + session
├── main.py             FastAPI app factory
├── worker.py           arq WorkerSettings
├── models/             SQLAlchemy ORM (Postgres 17 + PostGIS)
├── schemas/            Pydantic v2 DTOs
├── sources/            ingestion adapters (RESO/MLS, Zillow, PropertyRadar, ...)
├── enrichment/         photo grading, AVM, comps, skip trace, signals
├── scoring/            per-persona scorers (flipper, wholesaler, buyers_agent)
├── matching/           buyer <-> property matching
├── agents/             Claude Agent SDK wrappers (vision + reasoning)
├── pipeline/           normalize / dedup / orchestrate the DAG
├── api/                FastAPI routers + auth + middleware
├── jobs/               arq job definitions
└── utils/              addr, geo, money, hashing, retry
```

## Plugging into the Snoopy frontend

This service is data-plane only — it does not mint auth tokens and it
does not render UI. Snoopy plugs in via:

1. **Environment**
   - Set `JWT_JWKS_URL` (or `JWT_HS_SECRET`) so the API can verify the
     tokens Snoopy already issues. `JWT_ISSUER` and `JWT_AUDIENCE`
     must match Snoopy's mint side.
   - Set `CORS_ORIGINS` to the Snoopy origin.
2. **Endpoints Snoopy calls**
   - `GET  /leads?zip=&city=&persona=&min_score=&cursor=&limit=` —
     paginated, cursor-based list ordered by score.
   - `GET  /leads/{id}` — full deal analysis + score explanation.
   - `POST /leads/{id}/feedback` — accept / edit / dismiss for
     continuous eval.
   - `GET/POST/DELETE /searches` — saved searches.
   - `GET/POST/DELETE /buyer-profiles` — buyer-side matching.
   - `GET  /matches/property/{id}` — which of my active buyer profiles
     would want this property.
3. **User mapping**
   - Snoopy is expected to `POST` an insert into `app_user` (or via a
     lightweight admin endpoint you can add) mirroring its own user
     rows keyed by `external_id = <snoopy user id>`. Every API call
     resolves `sub` -> `app_user.external_id` -> `app_user.id`.
4. **Realtime updates (optional, future)**
   - The `outbox_event` table + a small relay job can push webhook
     events to Snoopy when new leads land — see `pipeline/outbox.py`.

## What runs where

* **Postgres 17 + PostGIS 3.5** — canonical store + geo queries.
* **Redis 7** — arq queue + short-term cache.
* **arq worker** — cron (daily 06:00 UTC sweep) + on-demand ingest /
  enrich / score jobs.
* **FastAPI** — synchronous read/write API for Snoopy.

## Vendor keys / adapter enablement

The API and worker boot without any vendor keys. Adapters silently
disable themselves when their credentials are absent, so the pipeline
degrades gracefully in dev/demo. See `.env.example` for the full list.

## Testing

* Unit tests are pure-Python, hit no I/O, and run in <1s. `make test`.
* Integration tests spin up Postgres + Redis via `testcontainers`.
  `make test-integration` (requires Docker).
* Golden-file tests pin the LLM prompt tool schema. Update by deleting
  the golden JSON and re-running.

## Coding standards

* Python 3.13, strict mypy, Ruff with the "S" (bandit) rules on.
* All money as integer cents. All timestamps timezone-aware UTC.
* All external I/O adapters implement a Protocol and are mockable.
* Structured logging (structlog + JSON in prod).
* Pre-commit runs formatter + linter + gitleaks on every commit.

## License

Proprietary.
