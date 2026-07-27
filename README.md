# Agents

A repository of independent agents and one orchestrator that calls them.

An agent here is something you **call**, not something you boot: one JSON
request in, one structured result out, with cost attached. The contract is
[`AGENT_PROTOCOL.md`](./AGENT_PROTOCOL.md) and it is the only thing the
orchestrator knows about any agent.

## Layout

```
.
├── AGENT_PROTOCOL.md   the contract — read this first
├── CONTRIBUTING.md     how to add your own agent
├── _template/          a working agent to copy. Start here.
├── orchestrator/       discovery, manifests, transport, CLI
├── tests/              contract and template tests
├── .claude/            skills, rules, hooks, reviewers, /raise-pr
├── docs/               roadmap and deeper notes
├── registry.yaml       which agents exist (paths only)
└── <agent-name>/       one folder per agent, each with its own agent.yaml
```

## Agents

| Agent | Status | Capabilities |
|---|---|---|
| [`realty-lead-gen`](./realty-lead-gen) | active | `grade_photos` |

`uv run agents list` prints this from the manifests, so it is never stale.

## Calling an agent

```bash
uv sync                                       # once, for the orchestrator
uv run agents list                            # what is here
uv run agents describe <agent>                # handshake — free, no network
uv run agents call <agent> <capability> --input '{"…": "…"}'
uv run agents check                           # describe every agent
uv run agents test                            # run each agent's own tests
uv run agents lint                            # run each agent's own lint
uv run agents new <name>                      # scaffold one from the template
uv run agents verify                          # every gate CI runs, at once
```

Every call runs the agent as a subprocess **in its own folder, with its own
environment**. That is what makes an agent's relative paths resolve, keeps one
agent's dependencies from breaking another's, and stops an agent seeing
credentials it never declared.

## Adding an agent

`cp -r _template my-agent`, then follow
[`CONTRIBUTING.md`](./CONTRIBUTING.md). Two examples exist on purpose:
`_template/` is the skeleton, `realty-lead-gen/` is a worked reference with
real dependencies, a database, and its own CI.

## Development

```bash
uv run pytest                                          # contract + template
uv run ruff check orchestrator tests _template
uv run mypy orchestrator                               # strict
uv run pre-commit install                              # optional, runs the above
```

Root tooling covers root-owned code only. Each agent lints and tests itself
from its own folder, with its own configuration — the same isolation that
applies to dependencies.

## CI

Workflows live in `.github/workflows/` at the root, because GitHub Actions
does not discover workflows nested inside a directory. Each agent's pipeline
is scoped with a `paths:` filter.

| Workflow | Runs on | Gate |
|---|---|---|
| `orchestrator.yml` | every change | ruff, strict mypy, registry validation, contract and template tests, then `agents check` |
| `realty-lead-gen.yml` | `realty-lead-gen/**` | ruff, mypy, unit tests, integration against real Postgres and Redis, coverage gate |
| `review.yml` | pull requests | the deterministic gates, then the review board — four reviewers reading the diff. Authenticates with `CLAUDE_CODE_OAUTH_TOKEN` (your Claude subscription, via `claude setup-token`) or `ANTHROPIC_API_KEY`; warns and skips with neither |

`agents check` calls `describe` on an agent — no network, no credentials, no
cost — and compares what the agent reports against its manifest. A capability
declared and not implemented, or implemented and not declared, fails here.
`agents test` then runs the agent's own declared test command in its own
folder, so a contributed agent's tests actually execute.

**It is scoped to what changed.** A pull request adding one agent builds and
describes that agent only; it does not build every other agent, and it cannot
go red because someone else's agent is broken. Changing `orchestrator/` or
the root project does sweep every agent, because shared code can break any of
them. A documentation-only change skips the job entirely.

Registry integrity is checked separately and statically by
`agents list --strict`, which costs nothing and catches the mistake new
contributors actually make: writing an agent and forgetting to register it.
Discovery ignores unregistered folders by design, so without that check the
agent would merge green and simply never be callable.

## Roadmap

[`docs/ROADMAP.md`](./docs/ROADMAP.md) is where this repository is going —
the contribution funnel, the guidance surface, multi-tenancy, runtime,
evaluation, and what is deliberately not being built yet.
[`docs/CERTIFICATION_ROADMAP.md`](./docs/CERTIFICATION_ROADMAP.md) is one
workstream of it in detail.

## How the repository guides you

Three mechanisms, each doing what only it can:

| | Does |
|---|---|
| **Skills** (`.claude/skills/`) | `/new-agent` interviews you before scaffolding |
| **Rules** (`.claude/rules/`) | Load automatically when you edit a manifest, an entrypoint, or shared code — contract reminders at the moment they apply |
| **Hooks** (`.claude/settings.json`) | Edit a manifest and the integration gate runs; a half-finished agent is reported before you get further |
| **Tools** (`agents` CLI) | `list`, `describe`, `call`, `check` — the same commands CI runs |

Rules guide, hooks enforce. A rule is context you may act on; a hook exits
non-zero and has to be dealt with.

## Licensing

No repository-wide license. Each agent folder carries its own;
`realty-lead-gen/LICENSE` is proprietary. A new agent should ship an explicit
`LICENSE` rather than inherit an assumption.
