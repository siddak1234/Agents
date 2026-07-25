# Agents

A monorepo of independent agents. Every agent lives in its own top-level
folder and owns its entire stack — language, dependencies, tests, docs, and
deployment. The repository root is reserved for the **agent orchestrator**:
the layer that discovers agents, routes work to them, and coordinates work
spanning more than one.

The orchestrator does not exist yet. What is here today is the structure it
will be built into, plus the first agent.

## Layout

```
.
├── README.md          you are here
├── CLAUDE.md          repo-wide conventions
├── registry.yaml      the agent list — source of truth for discovery
├── .gitignore         root-level ignores (read its header before editing)
├── .gitattributes     line endings + generated-file marking
└── <agent-name>/      one folder per agent
```

Adding an agent adds a folder at this level. Nothing else moves.

## Agents

| Agent | Status | Stack |
|---|---|---|
| [`realty-lead-gen`](./realty-lead-gen) | active | Python 3.13 · FastAPI · Postgres+PostGIS · Redis/arq |

Full details — summary, entrypoints, dependencies — live in
[`registry.yaml`](./registry.yaml). Each agent's own `README.md` is the place
to start when working on it.

## Adding an agent

1. Create a folder at the repository root named after the agent
   (`kebab-case`). It must not collide with a reserved root name — see
   [`CLAUDE.md`](./CLAUDE.md).
2. Give it everything it needs to stand alone: its own dependency manifest,
   tests, `README.md`, `CLAUDE.md` for its house rules, and a `.gitignore`
   for anything specific to its toolchain.
3. Add an entry to `registry.yaml`.
4. Add a row to the table above.

The rule of thumb: an agent folder should still make sense if someone copied
it out of this repo and ran it by itself. The orchestrator depends on agents;
agents do not depend on the orchestrator.

Steps 3 and 4 are not yet enforced by anything — no check verifies that the
registry, this table, and the folders on disk agree. Adding that check is
worth doing before the third agent lands.

## The orchestrator (not yet built)

The root is where cross-agent concerns will live once there is more than one
agent to coordinate. `registry.yaml` is the first piece: discovery by
explicit declaration rather than directory globbing, so creating a folder is
never by itself enough to activate an agent.

Deliberately unresolved until there is a second agent to design against:

- **Calling convention.** `realty-lead-gen` exposes a REST API and nothing
  else. Whether the orchestrator speaks HTTP to agents, imports them as
  libraries, or shells out is open, and answering it from a sample size of
  one would be guessing.
- **Shared runtime.** Each agent brings its own virtualenv today. Whether
  that stays true, or Python agents share a `uv` workspace, is likewise open.

## CI

There is no root-level CI yet, and this is a real gap rather than an
oversight: in a monorepo each agent needs a root workflow scoped with a
`paths:` filter, or its pipeline either never runs or runs on every commit
touching any agent.

`realty-lead-gen/.github/workflows/ci.yml` is a complete pipeline — lint,
typecheck, unit tests, integration against real Postgres and Redis, and a
combined coverage gate — but GitHub Actions only discovers workflows at the
repository root, so nested one level down it is **dormant**. It was left
unmodified so the agent folder stays self-contained. Activating it means a
root `.github/workflows/realty-lead-gen-ci.yml` with a `paths:` filter on
`realty-lead-gen/**` and adjusted working directory, uv cache glob, and
artifact paths.

## Licensing

There is no repository-wide license. Licensing is per agent: each agent
folder carries its own `LICENSE`, and `realty-lead-gen/LICENSE` is
proprietary. A new agent should ship with an explicit `LICENSE` of its own
rather than inheriting an assumption.
