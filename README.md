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

`agents check` is the integration gate worth understanding: it calls
`describe` on an agent — no network, no credentials, no cost — and catches a
broken entrypoint or a manifest that no longer matches its code.

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

[`docs/CERTIFICATION_ROADMAP.md`](./docs/CERTIFICATION_ROADMAP.md) records
what this repo teaches well, what it does not yet teach, and the order to
close that in.

## Licensing

No repository-wide license. Each agent folder carries its own;
`realty-lead-gen/LICENSE` is proprietary. A new agent should ship an explicit
`LICENSE` rather than inherit an assumption.
