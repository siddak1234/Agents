# Working in this repository

This is a monorepo of agents. Read this before making changes.

## The one structural rule

**Every agent lives in its own top-level folder. The repository root is the
agent orchestrator.**

That means:

- New agent → new folder at the repository root, `kebab-case`, plus an entry
  in `registry.yaml` and a row in the `README.md` table.
- Never put an agent's code at the root, and never nest one agent inside
  another.
- An agent folder should remain runnable if it were copied out of this repo
  on its own. Prefer duplicating a small amount of config inside an agent
  over introducing a dependency on the root.
- Root-level files are for cross-agent concerns only. If a change only
  affects one agent, it belongs inside that agent's folder.

## Before changing an agent

Work inside that agent's folder and follow *its* conventions, not a
repo-wide default. Agents differ deliberately — check the agent's
`README.md` for its commands and its `ARCHITECTURE.md`, if it has one, for
why things are the way they are.

Run that agent's own test and lint commands from its folder. There is no
root-level test runner, and adding one is an orchestrator decision, not a
convenience to reach for mid-task.

## Agent quick reference

### `realty-lead-gen`

Real-estate lead-generation backend. Python 3.13, FastAPI + async
SQLAlchemy, Postgres 17 with PostGIS, Redis with arq for background jobs,
Anthropic SDK for vision and reasoning. Dependencies via `uv`.

```bash
cd realty-lead-gen
make setup            # uv sync + pre-commit hooks
make test             # unit tests, fast, no docker
make test-integration # needs docker (spins up postgres + redis)
make lint             # ruff + mypy strict
```

House rules that apply inside this agent and are easy to violate by accident:

- Money is stored as **integer cents**, never floats.
- Timestamps are **timezone-aware UTC**, always.
- External I/O adapters implement a Protocol so they can be mocked; do not
  call a vendor SDK directly from business logic.
- `mypy` runs in strict mode and Ruff has the bandit (`S`) rules enabled.
- Mutable state is modelled as append-only snapshot tables, not in-place
  updates — the audit trail is a design goal, not an accident.

`realty-lead-gen/ARCHITECTURE.md` is the source of truth for design
decisions and explains the reasoning behind each of the above.

## The orchestrator

Not built yet. `registry.yaml` is the only orchestrator-level artifact so
far: agents are discovered by explicit declaration there, not by globbing
directories.

Do not invent an orchestrator interface and retrofit agents to it. The
calling convention should be designed once there is a second agent to design
against — with a sample size of one it would be a guess. If a task seems to
require an orchestrator contract, say so rather than inventing one.
