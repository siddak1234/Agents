# realty-lead-gen — Architecture

Backend for agentic real-estate lead generation. This document is the
single source of truth for what we're building, why we picked the
components we did, and where the biggest risks live. It is intentionally
opinionated — every choice below is defended with either a benchmark, a
2025-26 vendor fact, or a legal precedent, not "vibes."

Companion research produced in the same effort is summarized inline
with citations; the raw research notes are in the sibling deliverable
`research/` (not required to run the service).

## 1. Design goals

1. **A single canonical property object per real-world parcel**, deduped
   across MLS, portals, and county records. This is the moat: no
   incumbent maintains it well. Every enrichment attaches provenance
   to this record, never mutating history.
2. **Per-persona reasoning, not per-persona UI.** Flipper, wholesaler,
   and buyer's agent each get their own scorer + rubric + explanation,
   backed by the same data. The frontend switches persona; the backend
   swaps the scoring module.
3. **Auditability by default.** Every enrichment run, LLM call, and
   score component is stored. If a realtor asks "why did this appear?",
   the answer is one query away.
4. **Plug-and-play into Snoopy.** No auth issuance, no rendering,
   REST-only, RFC 7807 errors, CORS-aware.
5. **Cost discipline.** Every LLM call is metered (input/output tokens
   -> cost micros). A per-lead budget cap short-circuits runaway
   enrichment.
6. **Legal defensibility.** MLS access is licensed. Portal scraping is
   logged-out only (Meta v. Bright Data, N.D. Cal. 2024). Skip-tracing
   output is quarantined until DNC/TCPA scrub is performed.

## 2. High-level shape

```
                  ┌──────────────────────────────────────┐
                  │ Snoopy (React frontend + IDP)        │
                  └───────────────┬──────────────────────┘
                                  │  JWT (verified via JWKS)
                                  ▼
    ┌────────────┐         ┌───────────────┐         ┌──────────────┐
    │ FastAPI    │◀───────▶│ Postgres 17   │◀───────▶│ arq worker    │
    │ (read/write)         │ + PostGIS     │         │ (asyncio)     │
    └────────────┘         └───────────────┘         └──────────────┘
                                  ▲                          │
                                  │                          │  cron: daily 06:00 UTC
                                  │                          ▼
                                  │                 ┌──────────────────┐
                                  │                 │ Source adapters  │
                                  │                 │  RESO/MLS Grid   │
                                  │                 │  PropertyRadar   │
                                  │                 │  RapidAPI Zillow │
                                  │                 │  County recorder │
                                  │                 │  FSBO            │
                                  │                 └──────────────────┘
                                  │                          │
                                  │                          ▼
                                  │                 ┌──────────────────┐
                                  │                 │ Enrichment DAG   │
                                  │                 │ - Photos (Claude vision + UAD C1-C6) │
                                  │                 │ - AVM  (RentCast / HouseCanary swap) │
                                  │                 │ - Comps + LLM re-rank                │
                                  │                 │ - Signals (derived + sourced)        │
                                  │                 │ - Skip trace (stub)                  │
                                  │                 └──────────────────┘
                                  │                          │
                                  │                          ▼
                                  │                 ┌──────────────────┐
                                  │                 │ Scoring          │
                                  │                 │ - FlipperScorer  │
                                  │                 │ - WholesalerScorer│
                                  │                 │ - BuyersAgentScorer│
                                  │                 └──────────────────┘
                                  │                          │
                                  └──────────────────────────┘
                                            Leads
```

Three tiers, cleanly separated:

* **API tier** (`realty_lead_gen.main:app`) is read-mostly and stateless.
  Every write is a small mutation (feedback, saved searches, buyer
  profiles). Heavy work never runs here.
* **Storage tier** is Postgres 17 with PostGIS 3.5. Redis is the queue
  and short-term cache — it never holds source of truth.
* **Worker tier** is one arq process (`realty_lead_gen.worker`) that
  scales horizontally by adding replicas. It runs a daily cron + on-
  demand jobs enqueued by the API or upstream jobs.

## 3. The schema (why every table exists)

19 tables. Every mutable attribute lives on a separate append-only
snapshot/analysis table so we retain audit history and never lose the
"why did we see it this way at time T?" answer.

| Table | Purpose |
|---|---|
| `property` | Canonical, deduped real-world parcel. Keyed by `address_hash`. |
| `property_snapshot` | Time series of mutable property attributes (price, status, DOM). One row per ingest observation. |
| `listing` | An MLS or portal listing record — distinct from property (a parcel can have many listings over time). |
| `photo` | Photos attached to a property, with a perceptual hash for cross-listing dedup. |
| `photo_analysis` | Per-photo Claude vision output (UAD grade, findings, evidence). Append-only. |
| `owner` | Owner records with a type discriminator (individual, LLC, trust, etc.). |
| `property_ownership` | Time-bounded ownership intervals — supports historical ownership + fractional. |
| `contact_channel` | Owner phone/email/mailing enrichment. TCPA-sensitive (see §6). |
| `signal` | Motivated-seller signals attached to properties (NOD, tax delinquent, high equity, price cut, etc.). |
| `enrichment_run` | Row per enrichment attempt (success or failure), with cost, duration, idempotency key. |
| `deal_analysis` | Fused deal view — condition, rehab, AVM, ARV, comps, red flags, narrative. Versioned (append-only). |
| `score` | Per-persona property score. `(property, persona, scorer_version)` unique. |
| `lead` | Materialized surface — a `(property, user, persona)` triple above threshold. |
| `lead_feedback` | User feedback (accept/edit/dismiss) — training data for continuous eval. |
| `buyer_profile` | Buyer-side matching input for buyer's agent persona. |
| `saved_search` | User-configured recurring search (zip/city + persona + min score). |
| `app_user` | Local mirror of Snoopy users (keyed by `external_id`). |
| `audit_event` | Append-only security/compliance log. |
| `outbox_event` | Transactional outbox (see §7). |

Every non-audit table has `created_at`/`updated_at` (timezone-aware).
Every constraint uses the SQLAlchemy naming convention so Alembic
autogenerate produces stable migrations.

## 4. Sources — what we ingest, where from

Recommendation ranked by production-readiness, per the accompanying
research:

1. **RESO Web API via Trestle or MLS Grid** (`sources/reso_mls.py`).
   Licensed, resellable data. Requires a broker of record + vendor
   agreement per MLS. Budget $2–8k/mo for national-ish coverage.
2. **PropertyRadar** (`sources/propertyradar.py`) — off-market lists
   (NOD, tax delinquent, high equity, absentee). $599/mo Business
   tier gives API. Zero legal risk (licensed, aggregator-of-record).
3. **County recorder scraping** (`sources/county_recorder.py`) —
   deferred until the moat matters (post-Series A). ~3,100 counties,
   $500k+ engineering to industrialize.
4. **RapidAPI Zillow endpoint** (`sources/rapidapi_zillow.py`) —
   *reference/demo only*. Brittle (community-maintained, providers get
   blocked quarterly). Ship replaces with **Bright Data Web Unlocker
   + logged-out portal fetch** for production Zillow/Redfin/
   Realtor.com fill-in. Legal posture: Meta v. Bright Data (N.D. Cal.
   2024) neutralizes ToS for logged-out data; copyright over MLS
   photos still binds, so we never republish photos.
5. **FSBO / auction** (`sources/fsbo.py`) — scaffold; add when a
   persona-specific need emerges.

Every adapter satisfies `sources.base.SourceAdapter` (a Protocol), is
mockable, and self-disables when its credentials are absent.

## 5. Enrichment — the actual value creation

### 5.1 Photo grading — `enrichment/photos.py` + `agents/photo_grader.py`

* **Model**: Anthropic Claude Sonnet 4.5 with vision (`ANTHROPIC_MODEL_VISION`).
  Restb.ai remains the specialized fallback for any decision that
  touches a lender contract (they publish 98% agreement w/ appraisers
  in a Fannie Mae internal validation, May 2025).
* **Rubric**: Fannie Mae UAD 3.6 whole-property condition C1..C6, plus
  per-system decomposition (kitchen, bath, roof, HVAC, etc.). This is
  the same rubric appraisers use — everything downstream (lender
  eligibility, insurance) is compatible.
* **Structured output**: Anthropic tool-use enforces schema, so we
  never parse free text. See `photo_grader._PHOTO_GRADER_TOOL`.
* **Evidence citation**: every repair item must cite at least one
  `evidence_photo_id`. LLM-only inferences without evidence are
  flagged low-confidence and gated for human review.
* **Cost discipline**: 8 photos per LLM call, worst-of aggregation
  across batches, `LLM_MAX_COST_PER_LEAD_USD` circuit breaker.
* **Prompt versioning**: `PROMPT_VERSION = "photo_grader_v1"`. Every
  `PhotoAnalysis` row records the model_id and prompt_version so old
  data can be re-scored deterministically after a prompt change.
* **Golden-file tested**: `tests/unit/test_photo_prompt.py` pins the
  tool schema.

### 5.2 Valuation — `enrichment/valuation.py`

RentCast reference implementation. Interface designed so HouseCanary,
ATTOM, or CoreLogic can drop in behind it. AVM output is stored with
confidence and provider so downstream scoring can weight sources.

### 5.3 Comps — `enrichment/comps.py`

Two-stage: (a) retrieve ~20 candidates from the AVM provider, (b) ask
Claude to pick the 5–8 best and explain adjustments. Falls back to raw
candidates when Claude is unavailable. Per the research, LLM re-rank
adds real signal (5–15% MAPE improvement) on ambiguous cases and
adds nothing on cookie-cutter tract homes; the pipeline runs both
without extra cost since candidate retrieval is required anyway.

### 5.4 Motivated-seller signals — `enrichment/signals.py`

Two paths:
* **Derived** (computed here from existing snapshots): aged listing,
  recent price cut, high equity, long-term ownership, withdrawn
  recently.
* **Sourced** (attached by source adapters): NOD, lis pendens, tax
  delinquent, code violation, vacancy, probate.

Downstream scoring aggregates via a soft-OR
`1 - product(1 - strength_i * weight_i)` so signals reinforce rather
than double-count.

### 5.5 Skip trace — `enrichment/skip_trace.py`

Scaffold only at MVP. BatchSkipTracing reference (pay-per-hit,
~$0.20/hit at scale, ~70% match rate on RE data). **Contact channels
returned from skip trace are quarantined until they clear a DNC scrub
+ TCPA-appropriate consent gate before they can be used for outbound
communications.** The schema supports this today (`ContactChannel.is_dnc`);
the workflow lives in code we haven't shipped at MVP because MVP is
lead surfacing, not outbound.

## 6. Scoring — the persona-specific brain

Every scorer is deterministic and takes a fully-hydrated `PropertyContext`.
LLM narratives are generated separately (`scoring/explanations.py`) so
scoring stays unit-testable and reproducible.

### 6.1 Flipper — `scoring/flipper.py`

Weighted composite of six inputs. The heart is the "70% rule":

```
MAO = 0.70 * ARV - RehabHigh
```

The flipper scoring blend:

| Component | Weight |
|---|---|
| deal_gap (list vs MAO) | 0.45 |
| rehab_confidence (tight cost band) | 0.12 |
| arv_confidence (from AVM) | 0.13 |
| red_flag_penalty (foundation, roof, structural) | 0.10 |
| days_on_market (long DOM = flexibility) | 0.10 |
| condition_upside (C4/C5 preferred; C1/C2 low upside) | 0.10 |

Property-based tests (via `hypothesis`) confirm the composite stays
in `[0, 1]` across arbitrary numeric inputs.

### 6.2 Wholesaler — `scoring/wholesaler.py`

```
end_buyer_MAO = 0.70 * ARV - RehabHigh
spread        = end_buyer_MAO - list_price - assignment_fee
```

Blend:

| Component | Weight |
|---|---|
| assignability_spread | 0.40 |
| motivation_strength (signals soft-OR) | 0.30 |
| equity_strength | 0.15 |
| contactability (RPC probability) | 0.10 |
| competition_penalty (recent activity) | 0.05 |

`assignment_fee_cents` is per-user-configurable (defaults to $10k).

### 6.3 Buyer's agent — `scoring/agent.py`

Hard-criteria match against `BuyerProfile` (bedrooms, baths, price,
sqft, geo). Deal-breakers zero the score. Price-position bonus for
under-market listings. Fresh-listing bonus (DOM < 60d).

### 6.4 Explanation

`scoring/explanations.py` wraps a scored context into a Claude prompt
that produces a 2-3 sentence "why is this a good deal for you"
paragraph, citing concrete numbers. Never invented facts — the
prompt is explicit that the LLM must cite what's in front of it.

## 7. Orchestration + reliability patterns

* **arq** for background jobs — asyncio-native, Redis-backed. Chosen
  over Celery for asyncio ergonomics; over Temporal because we don't
  need Temporal's durable-workflow guarantees yet, and its operational
  cost is significant for a pre-seed footprint.
* **Transactional outbox** (`models/outbox.py`, `pipeline/outbox.py`)
  — any state mutation that needs to emit an event inserts an
  `OutboxEvent` in the same DB transaction. `jobs/outbox_relay.py` drains
  the table on a cron tick, claiming rows with `SELECT ... FOR UPDATE SKIP
  LOCKED` so every worker in the fleet can run the same job without two of
  them dispatching one event. Delivery is HMAC-signed; failures record an
  attempt count and back off rather than blocking the queue head. With no
  `OUTBOX_WEBHOOK_URL` configured the relay still drains — to the log —
  so a frontend-less deployment cannot grow the table without bound. This
  is the correct pattern for reliable at-least-once delivery without
  dual-writes. Reference: Chris Richardson, microservices.io.
* **Idempotency keys** on every enrichment run so retries are safe.
* **Retry policy** centralised in `utils/retry.py` — 5 attempts with
  exponential backoff + jitter, only on `TransientError`. `PermanentError`
  short-circuits (do not retry auth failures, 4xx client errors, etc.).
* **Per-adapter self-disable** — no adapter throws when unconfigured;
  it silently produces an empty stream. This is how the whole system
  boots without any vendor keys.
* **Per-subject rate limiting** (`api/ratelimit.py`) — a route
  dependency, not middleware, because it keys on the token's `sub`
  claim and that only exists after dependency resolution. IP keying
  would be wrong here: every caller arrives through Snoopy, so one
  office behind NAT would share a bucket while a stolen token stayed
  unthrottled. Built directly on `limits.aio` rather than `slowapi`
  (whose hit path is synchronous and whose key function cannot see the
  authenticated subject). Moving window, so there is no 2x burst at a
  window boundary; counters in Redis so the quota is shared across API
  workers rather than multiplied by them; **fails open** when Redis is
  unreachable, because a rate limiter is a budget control, not an
  authorization control, and failing closed turns a cache blip into a
  full outage. Breaches return RFC 7807 with `Retry-After`. Health
  probes are exempt, and anonymous traffic is out of scope by design —
  it is rejected by token verification before reaching the guard, so
  flood protection for it belongs at the edge (proxy / gateway / WAF).

## 8. Testing strategy

Counts in this section are measured, not estimated: **207 tests — 157
unit, 50 integration** (`pytest -m unit`, `pytest -m integration`).

* **Unit tests** (`tests/unit/`, 15 modules) — pure Python, zero I/O.
  Scoring (flipper, wholesaler, buyer's agent), address
  normalization, money math, cursor pagination, buyer matching,
  signal derivation, config guardrails, rate-limit policy, HTTP
  status triage, the `JSONDict` OpenAPI alias, the photo prompt, and
  the source-adapter conformance suite. Run in under 2s. `make test`.
* **Property-based tests** (`hypothesis`) on the flipper and buyer's
  agent scorers, to catch edge-case underflows / overflows / range
  violations that example-based tests miss.
* **Golden-file test** on the photo grader tool schema
  (`tests/golden/photo_grader_tool_schema.json`) — any change to the
  LLM contract has to be an explicit update to that file.
* **Integration tests** (`tests/integration/`, 5 modules) — run
  against a real PostGIS-enabled Postgres: the migration chain
  (`test_migrations.py`, 2), one end-to-end lead lifecycle
  (`test_lead_lifecycle.py`, 1), the whole `GET /leads` contract
  (`test_leads_api.py`, 25 cases covering tenancy isolation, every
  filter, the score boundary, limit bounds, and cursor walks to
  exhaustion), the scoring job (`test_score_job.py`, 17 — see below),
  and the rate limiter (`test_rate_limit_api.py`, 5).
  `make test-integration`.
* **The schema under test is built by Alembic, not by
  `create_all`.** `create_all` reads the same model metadata the
  tests import, so it can only ever agree with itself; running the
  real migration chain means the suite fails for the same reason
  production would.
* **Database selection is `TEST_DATABASE_URL`, then `DATABASE_URL`,
  then a `testcontainers` PostGIS container.** The container is the
  fallback, not the default — CI and a laptop with a local server
  both take the first branch. Redis is never containerized: the
  rate-limit tests exercise the same strategy and decision code
  against an in-memory store, and the Redis-specific concern
  (fail-open when the store is unreachable) is asserted directly by
  pointing the limiter at a dead port.

* **Adapter conformance suite** (`tests/unit/test_source_registry.py`,
  23 cases) — parameterized over the registry rather than over a list
  of classes, so a sixth adapter that has not been taught the rules
  fails here instead of in production. It asserts the preference order
  (which is dedup precedence, not decoration), that no adapter
  self-enables without a credential, that supplying exactly one
  credential enables exactly one adapter — the assertion that catches
  a gate wired to a neighbour's token — that the two deferred adapters
  stay dark even when handed every credential, and that `fetch`
  returns an async *iterator* rather than a coroutine and drains to
  empty rather than raising when unavailable. That last one is why
  `SourceAdapter.fetch` is declared `def` and not `async def`; before
  this suite existed the distinction was enforced only by mypy against
  the Protocol, which checks signatures and not behaviour. It also
  pins the stub/implemented split of §11 in executable form, so an
  adapter that grows a real body fails the test that says it yields
  nothing and forces the doc to be corrected in the same commit.

* **The scoring job under a real database**
  (`tests/integration/test_score_job.py`, 17 cases) — the deterministic
  math the whole product rests on, exercised through `score_property_job`
  against Postgres rather than through the scorers in isolation, because
  the defects it was written to catch do not live in the arithmetic. It
  covers all three personas, the persona-threshold cutoffs, dismissed
  leads being refreshed rather than resurrected, a buyer the matcher
  rejects producing no buyer's-agent score at all, and one case whose
  whole purpose is to run the same inputs in two different orders. It
  took `jobs/score.py` (155 statements, 48 branches) from 0% to
  **100%**, and it found two production bugs on the way — see below.

**Two bugs this suite found, both the same shape.** Neither was a
mistake in the scoring math, and neither would have failed a type
check, a lint, or a reading. Both were *unordered `SELECT`s whose row
order silently decided a user-visible value*. In the first,
`_buyers_agent_thresholds` built a dict comprehension over saved
searches, so when one agent had two searches with different minimum
scores, whichever row Postgres returned last set the threshold — the
lead appeared or did not appear depending on the plan; it is now a
`min()` fold. In the second, `_score_buyers_agent` picked the best
buyer with `if best is None or output.score > best.score` over a
profile list loaded with no `ORDER BY`, so two of an agent's buyers
with identical criteria — an ordinary thing, one profile copied for a
similar client — scored identically and "first maximum wins" handed
the choice to row order. The score never moved, but `components` and
`rationale` did: the explanation the agent reads for *why this house
fits* would change between nightly sweeps with nothing having
happened. It is now `max(..., key=lambda pair: (pair[1].score,
pair[0]))`, ties breaking on the profile id, and `matched_profile_ids`
is sorted at the producer so the audit trail stops rewriting itself.
Both fixes were proven the same way: restore the old body, watch the
new test fail, restore the fix, watch it pass. The general lesson is
recorded here because it will recur — **in this codebase, a `SELECT`
without an `ORDER BY` feeding anything that reduces to one value is a
bug waiting for a query plan to change.**

**Coverage, honestly.** Combined unit + integration line coverage is
**72.24%** (unit alone 65.35%, integration alone 66.32%). CI measures
the two halves in separate jobs, `coverage combine`s them, and
enforces the gate once over the union — judging either half against a
whole-repo threshold measures the split rather than the tests. The
threshold in `pyproject.toml` is a **ratchet set to the floor the
suite actually clears**, not an aspiration: an 80 that nothing meets
is a red build, and a red build is a gate nobody reads. The 60 → 70
move was bought by `tests/unit/test_lead_materialization.py` (33
cases) and `tests/integration/test_score_job.py` above.

The 70 → 72 move was not bought by a test, and the distinction is
worth stating plainly. `api/routes/leads.py` read **29.06%** while 25
passing tests drove it through the real ASGI app, and the missing-line
pattern was diagnostic once looked at: in every handler, coverage
reported the statements *after* the first `await session.execute(...)`
as never executed. Two things are measured here and one is inferred,
and they are worth keeping apart. **Measured:** the code does run —
a canary writing to a file, planted in the exact region coverage
called unreached, fired **47 times** in a run that still reported
those lines as missed. **Measured:** setting `concurrency =
["greenlet"]` moves that file from 29.06% to **75.21%** and moves
nothing else, an 85-file before/after diff over the full suite showing
zero regressions and exactly one change. **Inferred:** that the cause
is SQLAlchemy's asyncio layer running the synchronous ORM inside a
greenlet (`greenlet_spawn`) and coverage.py losing the caller's frame
across that switch. The inference is strong — the untraced region
begins precisely at the first statement after a database `await`, and
the option that fixes it is the one coverage.py ships for greenlet —
but it is read off the symptom, not out of coverage.py's internals.
Nothing downstream depends on which mechanism is right; the two
measured facts are enough. So the repo did not get better tested; it
got honestly measured. **Any coverage number taken from this project
without that setting understates request-handler coverage and should
be disbelieved.**

The remaining gap is concentrated and named rather than diffuse. Five
modules are still at **zero** — `worker.py` (29 statements),
`jobs/outbox_relay.py` (26), `enrichment/skip_trace.py` (25),
`scoring/explanations.py` (23) and `jobs/daily_sweep.py` (14) — the
first three plus `daily_sweep` being arq wiring that only a running
worker exercises. Below 40% and for the same underlying reason —
nothing yet drives ingest → enrich → score end to end without live
credentials — sit `utils/geo.py` (22.69%), `jobs/enrich.py` (26.97%),
`jobs/ingest.py` (27.91%), `pipeline/orchestrator.py` (29.91%),
`db.py` (32.43%), `agents/claude_client.py` (34.12%),
`sources/rapidapi_zillow.py` (34.19%), `enrichment/photos.py`
(36.23%), `enrichment/comps.py` (38.00%), `pipeline/outbox.py`
(38.27%) and `enrichment/valuation.py` (38.54%). The three
CRUD route modules — `searches.py` (41.30%), `buyers.py` (45.24%),
`matches.py` (45.71%) — are the cheapest remaining win, and the fact
that the greenlet fix did *not* move them is itself the evidence that
their handler bodies genuinely never run. Writing those tests raises
the number; editing the threshold does not.

## 9. Coding standards

* **Python 3.13** (strict mypy on `src/`, pydantic mypy plugin).
* **Ruff** with the "S" (bandit) rules on, plus TCH, PL, TRY, PERF, LOG.
* **Money as integer cents**, never floats.
* **Timestamps timezone-aware UTC**.
* **`from __future__ import annotations`** everywhere so forward refs
  are cheap.
* **Structlog** with contextvars — request_id and user_id survive
  across `await` boundaries and land on every log line.

## 10. Legal / compliance posture

* **MLS/IDX** data is used only under license from the broker of record
  (the SaaS is a "technology vendor" attached to that participant).
  Never republish photos or agent remarks.
* **Portal scraping** is logged-out only. Never account-holder-bound.
  Meta v. Bright Data (2024) precedent supports this posture; CoStar's
  copyright hard-line remains a real risk vector, so we cache raw
  payloads only in `raw_payload` and never re-serve verbatim.
* **TCPA / DNC**: `ContactChannel.is_dnc` gates outbound. Under the
  2023 FCC one-to-one consent rule (effective 2025), skip-traced
  contacts cannot be dialled without per-seller written consent. MVP
  surfaces contacts but does not initiate outbound.
* **Fair housing**: never source scoring signals from protected-class
  proxies (school district composition, religious institution density,
  etc.). Buyer-side matching uses only user-declared criteria.

## 11. What's stubbed vs live

Every number and status below is **measured, not remembered**.
`scripts/measure_surface.py` boots the app from settings and reads the
answers off real objects — `Base.metadata.tables`, the OpenAPI
document FastAPI actually generates, arq's `WorkerSettings` class
attributes, and each adapter's own `available` property evaluated
against a credential-free `Settings`. Re-run it after any change to
this section:

```bash
python scripts/measure_surface.py            # writes ./surface.json
python scripts/measure_surface.py out.json   # or a path you choose
```

It writes a file rather than printing, because building the app
configures structlog against the real `sys.stdout` and a log line
landing mid-document would break `| jq` on otherwise valid JSON. Two
runs can also be diffed, which is the point when auditing this
section. The output is gitignored.

The distinction the table draws is deliberate. **Implemented** means
the code path is written and typed but self-disables without a
credential — it is real code waiting on a key, and it degrades to a
no-op rather than an error. **Stub** means the entry point exists and
returns a well-typed nothing *even when credentialed*. **Deferred**
means `available` is hardcoded `False` and `fetch` yields nothing.
Calling all three "Live" is how an architecture doc starts lying.

An earlier draft of this table did exactly that, and it is worth
recording how it was caught. Three adapters were labelled
"Implemented" from memory. Writing
`tests/unit/test_source_registry.py` — which asserts what each `fetch`
actually yields when credentialed — showed that two of them return
nothing with a valid token in hand. By the definitions above they are
stubs, and the rows below now say so. The lesson generalizes: a status
column is a claim, and the only status claims worth trusting are the
ones something executes.

| Component | Status | Gate | Measured detail |
|---|---|---|---|
| Postgres schema | Live | — | **19 tables**, PostGIS; the Alembic chain was applied to a virgin cluster to verify it, not just to an already-migrated one |
| FastAPI app | Live | — | **10 paths / 12 operations**; JWT verify, RFC 7807 problem documents, keyset cursor pagination |
| arq worker | Live | — | **5 registered jobs** (`ingest_region`, `enrich_property`, `score_property`, `daily_sweep`, `outbox_relay`); **2 cron entries**; `max_jobs=20`, `job_timeout=600` |
| — daily sweep cron | Live | — | 06:00 UTC daily, `unique=True` so one worker in the fleet fires it |
| — outbox relay cron | Live | — | every minute at `second=0`, `max_tries=1`, `timeout=120` |
| RESO/Trestle adapter | **Stub** | `RESO_TRESTLE_TOKEN` | The gate works — the token flips `available` to `True` — but `fetch` then logs `reso_mls.not_implemented` and yields nothing. The OData query plan, the Data Dictionary mapping and the `ModificationTimestamp` delta strategy are written down in the module; the HTTP call is not. First in registry preference order, which is where it will matter once written |
| PropertyRadar adapter | **Stub** | `PROPERTYRADAR_API_TOKEN` | Same shape: gate wired, `fetch` logs `propertyradar.not_implemented` and yields nothing. Second in preference order |
| RapidAPI Zillow adapter | Implemented | `RAPIDAPI_KEY` | **The only adapter with a real `fetch` body** — retrying httpx client, `/propertyExtendedSearch`, status/type maps, `limit` honoured as a hard budget across queries, unparseable records skipped rather than raised. Prototype-grade by intent: these endpoints scrape Zillow behind the scenes and get blocked on quarterly timescales. Do not ship it as the production path |
| County recorder | Deferred | hardcoded `available = False` | `fetch` logs and yields nothing; per-county scraping is a $500k+ project |
| FSBO adapter | Deferred | hardcoded `available = False` | Same shape; route through an unblocker when scoped |
| Adapter registry | Live | — | `all_available_adapters()` returns **`[]` with no credentials** — the pipeline degrades to a no-op rather than failing. Conformance-tested: 23 cases, `sources/registry.py` at 100% |
| Photo grader | Implemented | `ANTHROPIC_API_KEY` | Claude vision, UAD C1–C6, tool-use structured output; a failed batch is logged and skipped, so an unkeyed run yields no analysis rather than an exception |
| RentCast AVM | Implemented | `RENTCAST_API_KEY` | Also produces the rent estimate |
| RentCast comps | Implemented | `RENTCAST_API_KEY` | Optional Claude re-rank on top |
| Skip trace | **Stub** | `BATCHSKIPTRACING_API_KEY` | `lookup()` returns `None` and logs `skip_trace.not_implemented` **even with the key set**. The contract and the DNC/reassigned-number gating are designed; the HTTP call is not written |
| Signal detection | Live (derived path) | — | Price cuts, aged listings, withdrawn, equity — computed from snapshots we already hold. Sourced signals (NOD, lis pendens, tax delinquency) arrive attached by an adapter, so they inherit that adapter's status |
| Flipper scorer | Live | — | Deterministic; hypothesis-tested |
| Wholesaler scorer | Live | — | Deterministic |
| Buyer's agent scorer | Live | — | Deterministic; hypothesis-tested |
| Buyer intent matching | Live | — | Backs `GET /matches/property/{id}`; user-declared criteria only |
| Explanation narratives | Live | optional `ANTHROPIC_API_KEY` | Genuinely optional: returns the deterministic component rationale when Claude is unavailable |
| Outbox relay | Live | optional `OUTBOX_WEBHOOK_URL` | `SELECT … FOR UPDATE SKIP LOCKED` claim, HMAC-signed POST; drains to the log when no URL is set |
| Rate limiting | Live | optional `REDIS_URL` | `limits.aio` moving window keyed on the JWT `sub`, Redis-shared, fails open |

**Nothing self-enables.** With an empty environment every credentialed
path reports unavailable and the service still boots, serves, and
returns empty result sets. That is the property the integration suite
depends on, and it is why `tests/conftest.py` deletes every vendor key
from the environment before a test runs.

### The OpenAPI contract the frontend generates against

`GET /openapi.json` emits **20 component schemas**. One of them is
worth calling out because a client generator will trip over it
otherwise: `JSONDict` is emitted as

```json
{ "type": "object", "additionalProperties": true }
```

and is referenced as `$ref: "#/components/schemas/JSONDict"` from
`SavedSearchCreate.criteria` and `SavedSearchDTO.criteria`. It is a
named alias rather than an inline `Dict[str, Any]` on purpose: an
inline mapping generates as an anonymous type per use site, so the
same saved-search payload would come out of the generator as two
unrelated types that cannot be assigned to each other. Naming it once
means the frontend gets a single `JSONDict` and round-trips a saved
search without a cast. Regenerate the client whenever this section's
schema count changes.

Current surface, verbatim from the generated document:

| Method | Path |
|---|---|
| GET | `/healthz` |
| GET | `/readyz` |
| GET | `/leads` |
| GET | `/leads/{lead_id}` |
| POST | `/leads/{lead_id}/feedback` |
| GET | `/buyer-profiles` |
| POST | `/buyer-profiles` |
| DELETE | `/buyer-profiles/{profile_id}` |
| GET | `/searches` |
| POST | `/searches` |
| DELETE | `/searches/{search_id}` |
| GET | `/matches/property/{property_id}` |

## 12. What we deliberately did NOT do

* **No LangChain / CrewAI abstraction layer.** Claude's own SDK plus
  small hand-rolled orchestration wins on transparency, cost, and
  debuggability for our scale. See research on framework failure modes.
* **No embedded browser scraping.** Playwright-in-process is heavier
  than needed at MVP; when we scrape Zillow we do it via Bright Data
  Web Unlocker (managed anti-bot).
* **No n8n orchestrator.** Considered heavily per your earlier
  guidance. Rejected for this backend because the audit surface we
  need is per-property and record-linked (all in Postgres), not
  per-workflow-execution. If you want n8n around this for
  cross-system business workflows (e.g. "on Lead created, send Slack
  to agent"), it fits cleanly *outside* this service, calling our REST
  endpoints and reacting to outbox webhooks.
* **No Supabase** at MVP. Your call; we can add Supabase for auth or
  for Realtime later without schema changes — RLS policies would map
  cleanly to the existing user_id foreign keys.
* **No user-visible payments or subscription logic.** Owned by Snoopy.
