# Working in this repository

A monorepo of agents. This file covers what is true repo-wide. Conventions
that belong to a single agent live in that agent's own `CLAUDE.md`, which
Claude Code loads when it reads files in that folder — so this file stays
short no matter how many agents are added.

## The one structural rule

**Every agent lives in its own top-level folder. The repository root is the
agent orchestrator.**

- New agent → new folder at the repository root, `kebab-case`, plus an entry
  in `registry.yaml` and a row in the `README.md` table.
- Never put an agent's code at the root, and never nest one agent inside
  another.
- An agent folder should remain runnable if it were copied out of this repo
  on its own. Prefer duplicating a little config inside an agent over
  introducing a dependency on the root.
- Root files are for cross-agent concerns only. If a change affects one
  agent, it belongs inside that agent's folder — including its `CLAUDE.md`,
  its `.gitignore`, and its CI.

### Reserved root names

An agent folder must not collide with root infrastructure. Currently taken:
`.git`, `.github`, `.gitignore`, `.gitattributes`, `CLAUDE.md`, `README.md`,
`registry.yaml`. Avoid generic names likely to be claimed later — `scripts`,
`docs`, `tools`, `shared`.

## Before changing an agent

Work inside that agent's folder and follow *its* conventions, not a repo-wide
default. Agents differ deliberately. Read the agent's `CLAUDE.md` for its
house rules, its `README.md` for commands, and its `ARCHITECTURE.md`, if it
has one, for why things are the way they are.

Run that agent's own test and lint commands from its folder. There is no
root-level test runner, and adding one is an orchestrator decision, not a
convenience to reach for mid-task.

## Where facts about agents live

`registry.yaml` is the source of truth for which agents exist and how each is
started. The `README.md` table is a human-readable index of the same set.
Nothing else should enumerate agents — a third list is a third thing to
forget to update.

Nothing currently validates that `registry.yaml`, the README table, and the
folders on disk agree. Until something does, changing one means changing all
three.

## Editing the root `.gitignore`

Read the comments at the top of that file before adding a pattern. Two
patterns that look obviously correct — `.env.*` and an unanchored `tmp/` —
silently swallow files agents need committed, and the failure is invisible
until someone else clones the repo. When in doubt, put the pattern in the
agent's own `.gitignore` where its blast radius is one folder.

## The orchestrator

Not built yet. `registry.yaml` is the only orchestrator-level artifact so
far: agents are discovered by explicit declaration there, not by globbing
directories, so creating a folder is never by itself enough to activate an
agent.

Do not invent an orchestrator interface and retrofit agents to it. The
calling convention should be designed once there is a second agent to design
against — with a sample size of one it would be a guess. If a task seems to
require an orchestrator contract, say so rather than inventing one.
