# Working in this repository

Repo-wide rules. Conventions belonging to one agent live in that agent's own
`CLAUDE.md`, which loads when you read files in its folder — so this file
stays short however many agents exist.

The contract is [`AGENT_PROTOCOL.md`](./AGENT_PROTOCOL.md). The procedure for
adding an agent is [`CONTRIBUTING.md`](./CONTRIBUTING.md). Do not restate
either here; a third copy is a third thing to forget to update.

## "Agent" means one thing here

Two different populations live in this repo. Keep them apart in your head and
in your writing, or every instruction becomes ambiguous.

| | **Agents** | **Reviewers** |
|---|---|---|
| Are | `realty-lead-gen` and its peers | `agent-architect`, `engineer-reviewer`, … |
| Live in | a folder at the repo root | `.claude/agents/` |
| Declared by | `agent.yaml` | Markdown + frontmatter |
| Invoked by | the orchestrator, subprocess + JSON | Claude Code, during review |
| Work on | the user's problem | this repository |
| Deterministic | yes | no |

They share no format, runtime, or contract. **"Agent" always means the first
kind.** The second are *reviewers* — never call them agents.

## The two structural rules

**1. Every agent lives in its own top-level folder. The root is the
orchestrator.**

**2. An agent is called, not booted.** If you find yourself adding ports,
health checks, or process supervision to make an agent reachable, stop — that
is service management and it is the wrong layer. An agent that also runs as a
service (as `realty-lead-gen` does) exposes that separately.

Consequences that are load-bearing:

- **The orchestrator must never import agent code, and an agent must never
  import the orchestrator.** The first keeps one agent's dependencies from
  breaking everything else; the second keeps an agent working when extracted.
- **Root tooling covers root-owned code only** — `orchestrator/`, `tests/`,
  `_template/`. Agents lint, type-check, and test themselves with their own
  configuration. Commands pass explicit paths, never `.`.
- **`orchestrator/` depends on the standard library plus `pyyaml`.** That is
  the budget. A dependency added there is one every agent pays for.
- Root files are for cross-agent concerns. Agent-specific anything —
  `CLAUDE.md`, `.gitignore`, CI standards — belongs in the agent's folder.

### Reserved root names

`.git`, `.github`, `.claude`, `.gitignore`, `.gitattributes`, `_template`,
`orchestrator`, `tests`, `docs`, `AGENT_PROTOCOL.md`, `CLAUDE.md`,
`CONTRIBUTING.md`, `README.md`, `pyproject.toml`, `registry.yaml`,
`uv.lock`, `.pre-commit-config.yaml`. Avoid generic names likely to be
claimed later — `scripts`, `tools`, `shared`.

**A leading underscore means "not an agent."** `agents list --strict` fails on
any folder holding an `agent.yaml` that is not registered — that is almost
always a half-finished integration — and skips `_`-prefixed folders, which is
what lets `_template` carry a real manifest without ever being callable.

`_template/` is a working agent, deliberately absent from `registry.yaml` so
it never appears in `agents list` as though it were real. `tests/` runs it
every build, including copying and renaming it the way `CONTRIBUTING.md`
says to.

## Before changing an agent's internals

Work inside its folder and follow *its* conventions. Read the agent's
`CLAUDE.md` for house rules, its `README.md` for commands, its
`ARCHITECTURE.md` if it has one. Run its own test and lint commands from its
folder — there is no root-level runner for agent code.

## Verifying a change

```bash
uv run agents check      # every agent still answers, and matches its manifest
uv run pytest            # contract and template
uv run ruff check orchestrator tests _template
uv run mypy orchestrator
```

`agents describe <agent>` against `agents describe <agent> --static` shows
whether a manifest has drifted from its code — the first runs the agent, the
second only reads YAML.

## Editing the root `.gitignore`

Read its header first. Two patterns that look obviously correct — `.env.*`
and an unanchored `tmp/` — silently swallow files agents need committed, and
the failure stays invisible until someone else clones the repo. When in
doubt, put the pattern in the agent's own `.gitignore`, where its blast
radius is one folder.

## Do not build ahead of evidence

Capability routing, agent-to-agent composition, cost aggregation, and other
transports are deliberately absent. With one agent, any interface for them is
guesswork. When a task seems to need one, say so and design it against the
second real agent rather than inventing it.
