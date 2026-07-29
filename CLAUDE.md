# Working in this repository

Repo-wide rules. Conventions belonging to one agent live in that agent's own
`CLAUDE.md`, which loads when you read files in its folder — so this file
stays short however many agents exist.

The contract is [`docs/AGENT_PROTOCOL.md`](./docs/AGENT_PROTOCOL.md); the
procedure for adding an agent is [`docs/CONTRIBUTING.md`](./docs/CONTRIBUTING.md).
Do not restate either here — a third copy is a third thing to forget.

## "Agent" means one thing here

Two different populations live in this repo. Keep them apart in your head and
in your writing, or every instruction becomes ambiguous.

| | **Agents** | **Reviewers** |
|---|---|---|
| Are | `realty-lead-gen` and its peers | `agent-architect`, `engineer-reviewer`, … |
| Live in | `agents/<name>/` | `.claude/agents/` |
| Declared by | `agent.yaml` | Markdown + frontmatter |
| Invoked by | the orchestrator, subprocess + JSON | Claude Code, during review |
| Work on | the user's problem | this repository |
| Deterministic | yes | no |

They share no format, runtime, or contract. **"Agent" always means the first
kind.** The second are *reviewers* — never call them agents.

## The two structural rules

**1. Every agent lives in its own folder under `agents/`. Everything else is
the platform.** One glance separates tenant space from platform space, and
`agents list --strict` reports a registered agent anywhere else. (Agents
used to live at the root; ten contributors' folders beside `orchestrator/`
is why they no longer do.)

**2. An agent is called, not booted.** If you find yourself adding ports,
health checks, or process supervision to make an agent reachable, stop — that
is service management and it is the wrong layer. A capability that truly
needs a running service means the service belongs in its own repository,
exposing an agent here (`realty-lead-gen` is the worked example: the service
moved out, the agent stayed).

Consequences that are load-bearing:

- **The orchestrator must never import agent code, and an agent must never
  import the orchestrator.** The first keeps one agent's dependencies from
  breaking everything else; the second keeps an agent working when extracted.
- **Root tooling covers platform code only** — `orchestrator/` (which holds
  its own tests) and `agents/_template/`. Real agents lint, type-check, and
  test themselves with their own configuration. Commands pass explicit paths,
  never `.`.
- **`orchestrator/` depends on the standard library plus `pyyaml`.** That is
  the budget. A dependency added there is one every agent pays for.
- Root files are for cross-agent concerns. Agent-specific anything —
  `CLAUDE.md`, `.gitignore`, CI standards — belongs in the agent's folder.

### Names

Because agents live in `agents/`, they can no longer collide with platform
names at the root — that is most of what the old reserved-names list
existed for. The scaffolder still refuses a handful of names that would
shadow tooling in commands and prose (`orchestrator`, `tests`, `registry`,
`docs`, `scripts`, `tools`, `shared`).

**A leading underscore means "not an agent."** `agents list --strict` fails on
any folder holding an `agent.yaml` that is not registered — that is almost
always a half-finished integration — and skips `_`-prefixed folders, which is
what lets `_template` carry a real manifest without ever being callable.

`agents/_template/` is a working agent, deliberately absent from
`registry.yaml` so it never appears in `agents list` as though it were real.
`orchestrator/tests/` runs it every build, including copying and renaming it
the way `docs/CONTRIBUTING.md` says to.

## Before changing an agent's internals

Work inside its folder and follow *its* conventions. Read the agent's
`CLAUDE.md` for house rules, its `README.md` for commands, its
`ARCHITECTURE.md` if it has one. Its test and lint commands are its own,
declared in its manifest — `uv run agents test <name>` and
`uv run agents lint <name>` run them from the agent's folder, which is also
what CI does.

## Verifying a change

```bash
uv run agents verify
```

One command, running every deterministic gate CI runs against the working
tree — including the format check, the secret scan and the large-file cap
that hand-maintained lists of "the gates" kept forgetting. `agents verify` is
the contributor-facing definition of the gates; the CI workflows run the same
set as their own steps, and a gate added to one belongs in both.

That last sentence is now a test rather than an aspiration. It was untrue
twice — gitleaks, then `check-added-large-files`, each added to CI and not to
`verify`, each leaving a window where a branch printed "all gates pass" and
went red anyway. `orchestrator/tests/test_verify.py` reads the workflow and
fails if a gate is in one and not the other.

One gate is deliberately outside `verify` and declared as such in
`_NOT_COVERED`: `agents scope` reads a *diff*, so it needs a base branch a
local checkout may not have, and CI enforces it by author. Run it yourself:

```bash
uv run agents scope --base origin/main
```

`agents describe <agent>` against `agents describe <agent> --static` shows
whether a manifest has drifted from its code — the first runs the agent, the
second only reads YAML.

## The layout

```
agents/        every agent, one folder each (+ _template to copy)
docs/          the contract, how to contribute, the intern brief, roadmap
orchestrator/  the platform: discovery, manifests, transport, CLI, its tests
.claude/       skills, rules, hooks, reviewers
```

Everything left at the root is there because a tool requires it —
`pyproject.toml` and `uv.lock` (uv), `.pre-commit-config.yaml`,
`.gitignore`/`.gitattributes` (git), `README.md` and this file. Add a root
file only when something outside the repository insists on finding it there.

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
