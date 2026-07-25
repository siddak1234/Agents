# Agents

A monorepo of independent agents. Every agent lives in its own top-level
folder and owns its entire stack — language, dependencies, tests, docs, and
deployment. The repository root is reserved for the **agent orchestrator**:
the layer that discovers agents, routes work to them, and coordinates work
that spans more than one.

The orchestrator does not exist yet. What is here today is the structure it
will be built into, plus the first agent.

## Layout

```
.
├── README.md          you are here
├── CLAUDE.md          conventions for working in this repo
├── registry.yaml      the list of agents — source of truth for discovery
├── .gitignore         root-level ignores only (see the file's header)
└── realty-lead-gen/   agent — real-estate lead generation backend
```

As agents are added, each one becomes another folder at this level. Nothing
else moves.

## Agents

| Agent | Status | Stack | What it does |
|---|---|---|---|
| [`realty-lead-gen`](./realty-lead-gen) | active | Python 3.13, FastAPI, Postgres+PostGIS, Redis/arq | Ingests real-estate listings from MLS, portals, and off-market sources; enriches them with Claude-powered condition grading, valuation, and comps; scores per persona (flipper / wholesaler / buyer's agent); materializes ranked leads. |

Each agent's own `README.md` is the place to start; `registry.yaml` holds the
same facts in machine-readable form.

## Adding an agent

1. Create a folder at the repository root named after the agent
   (`kebab-case`, matching its registry `name`).
2. Give it everything it needs to stand alone: its own dependency manifest,
   its own tests, its own `README.md`, its own `.gitignore` for anything
   specific to its toolchain.
3. Add an entry to `registry.yaml`.
4. Add a row to the table above.

The rule of thumb: an agent folder should still make sense if someone copied
it out of this repo and ran it by itself. The orchestrator depends on agents;
agents should not depend on the orchestrator.

## The orchestrator (not yet built)

The root level is where cross-agent concerns will live once there is more
than one agent to coordinate. `registry.yaml` is the first piece of that —
discovery by explicit declaration rather than by directory globbing, so that
creating a folder is never by itself enough to activate an agent.

Deliberately unresolved until there is a second agent to design against:

- **Calling convention.** `realty-lead-gen` currently exposes a REST API and
  nothing else. Whether the orchestrator talks HTTP to agents, imports them
  as libraries, or shells out to them is an open question, and answering it
  from a sample size of one would be guessing.
- **Shared runtime.** Each agent brings its own virtualenv today. Whether
  that stays true, or agents share a workspace, is likewise open.

## Known caveat: agent CI is currently dormant

`realty-lead-gen/.github/workflows/ci.yml` is a complete CI pipeline — lint,
typecheck, unit tests, integration tests against real Postgres and Redis, and
a combined coverage gate. GitHub Actions only discovers workflow files in
`.github/workflows/` **at the repository root**, so in its nested position
this workflow will not run.

It was left in place, unmodified, so the agent folder stays self-contained.
Making it run means lifting it to a root-level
`.github/workflows/realty-lead-gen-ci.yml` with a `paths:` filter scoped to
`realty-lead-gen/**` and the working directory, cache glob, and artifact
paths adjusted for the nested location. That has not been done.
