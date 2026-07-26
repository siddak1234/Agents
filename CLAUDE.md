# Working in this repository

A repository of callable agents. This file covers what is true repo-wide.
Conventions belonging to a single agent live in that agent's own `CLAUDE.md`,
which Claude Code loads when it reads files in that folder — so this file
stays short no matter how many agents are added.

## The two structural rules

**1. Every agent lives in its own top-level folder. The root is the
orchestrator.**

**2. An agent is called, not booted.** One JSON request in, one structured
envelope out, per [`AGENT_PROTOCOL.md`](./AGENT_PROTOCOL.md). If you find
yourself adding ports, health checks, or process supervision to make an
agent reachable, stop — that is service management, and it is not how agents
are invoked here. An agent that also happens to run as a service (as
`realty-lead-gen` does) exposes that separately; the service is one way to
deploy it, not what makes it an agent.

Concretely:

- New agent → new folder at the root, `kebab-case`, with an `agent.yaml`, a
  path in `registry.yaml`, and a row in the README table.
- Never put agent code at the root, and never nest one agent inside another.
- **The orchestrator must never import agent code, and an agent must never
  import the orchestrator.** Both directions are load-bearing: the first
  keeps one agent's dependencies from breaking everything else, the second
  keeps an agent working when extracted. `orchestrator/` depends on the
  standard library plus `pyyaml`, and that is the budget.
- Root files are for cross-agent concerns only.

### Reserved root names

An agent folder must not collide with root infrastructure. Taken: `.git`,
`.github`, `.gitignore`, `.gitattributes`, `_template`, `orchestrator`,
`tests`, `AGENT_PROTOCOL.md`, `CLAUDE.md`, `README.md`, `pyproject.toml`,
`registry.yaml`, `uv.lock`. Avoid generic names likely to be claimed later —
`scripts`, `docs`, `tools`, `shared`.

`_template/` is a working agent, deliberately absent from `registry.yaml` so
it never appears in `agents list` as though it were real. `tests/` still runs
it on every build, including copying it and renaming it the way the README
says to — a template that quietly stopped working would be worse than none,
because copying it is the first thing anyone does.

## Start a new agent by copying `_template`

Do not write one from scratch, and do not copy `realty-lead-gen` — it is a
130-file production service and a poor first thing to imitate. The template
is standard-library only, runs with no install step, and marks every spot
needing a change with `TODO(new agent)`.

## Implementing or changing an agent

Read [`AGENT_PROTOCOL.md`](./AGENT_PROTOCOL.md) first. The rules that get
broken by accident:

- **stdout carries the envelope and nothing else.** Usually broken by a
  dependency rather than by your code — `realty-lead-gen` configures
  structlog with `stream=sys.stdout`, so one log line makes the envelope
  unparseable. Point `sys.stdout` at stderr before doing any work and write
  the envelope to the real stdout captured at start. See
  `realty-lead-gen/src/realty_lead_gen/agentcall.py`.
- **Exit 0 whenever an envelope was produced**, including `ok:false`. A
  business failure is a successful call returning a failure.
- **A missing credential returns `unavailable`, never a crash.**
- **`describe` must not import heavy dependencies.**

Verify with `uv run agents check` and, for one agent,
`uv run agents describe <agent>` against `--static` — the first runs the
agent's code, the second reads its manifest, and a difference means the two
have drifted.

## Before changing an agent's internals

Work inside that agent's folder and follow *its* conventions. Read the
agent's `CLAUDE.md` for house rules, its `README.md` for commands, and its
`ARCHITECTURE.md` if it has one. Run its own test and lint commands from its
folder — there is no root-level test runner for agent code.

## Where facts about agents live

`agent.yaml` in the agent's folder is the source of truth for what an agent
is and how to call it. `registry.yaml` at the root holds paths and nothing
else. The README table is a human-readable index.

Nothing validates the README table against the registry. `agents check`
validates the registry against disk and against each agent's real behaviour.

## Editing the root `.gitignore`

Read the comments at the top of that file first. Two patterns that look
obviously correct — `.env.*` and an unanchored `tmp/` — silently swallow
files agents need committed, and the failure stays invisible until someone
else clones the repo. When in doubt, put the pattern in the agent's own
`.gitignore`, where its blast radius is one folder.

## Do not build ahead of evidence

Routing by capability, agent-to-agent composition, cost aggregation, and
additional transports are all deliberately absent. With one agent in the
repo, any interface for them would be guesswork. When a task seems to need
one, say so and design it against the second real agent rather than
inventing it.
