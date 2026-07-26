# Agents

A repository of agents that can be discovered and called. Every agent lives
in its own top-level folder and owns its entire stack. The repository root is
the **orchestrator**: it finds the agents and calls them.

An agent here is something you **call**, not something you boot — one JSON
request in, one structured result out, with usage attached. That contract is
[`AGENT_PROTOCOL.md`](./AGENT_PROTOCOL.md), and it is the only thing the
orchestrator knows about any agent.

## Layout

```
.
├── AGENT_PROTOCOL.md   the agentcall/v1 contract — read this first
├── _template/          a working agent to copy. Start here.
├── orchestrator/       discovery, manifest loading, transport, CLI
├── tests/              contract and template tests
├── .github/workflows/  CI for the orchestrator and for each agent
├── registry.yaml       which agents exist (paths only)
├── pyproject.toml      the orchestrator's own package
├── CLAUDE.md           repo-wide conventions
└── <agent-name>/       one folder per agent, each with its own agent.yaml
```

## Agents

| Agent | Status | Capabilities |
|---|---|---|
| [`realty-lead-gen`](./realty-lead-gen) | active | `grade_photos` |

`agents list` prints this from the manifests, so it is never stale.

## Calling an agent

```bash
uv sync                            # once, for the orchestrator itself
uv run agents list                 # what is here
uv run agents describe <agent>     # handshake — runs the agent, costs nothing
uv run agents call <agent> <capability> --input '{"...": "..."}'
uv run agents check                # describe every agent; non-zero if any fails
```

`check` is the one to wire into CI. It catches a registry that has drifted
from disk, a broken entrypoint, and a manifest that no longer matches its
agent — without spending anything.

Every call runs the agent as a subprocess **in the agent's own folder, using
the agent's own environment**. That is not an implementation detail: it is
what makes an agent's relative paths — `.env`, `alembic.ini` — resolve, and
it is why one agent's dependencies can never break another's or the
orchestrator's.

## Adding an agent

Copy the template. It is a complete working agent in ~100 lines of
standard-library Python, with every spot needing a change marked
`TODO(new agent)`.

```bash
cp -r _template my-agent
```

Then rename it in `agent.yaml` and `agent_main.py`, replace the example
capability with yours, add `- path: my-agent` to `registry.yaml` and a row to
the table above, and verify:

```bash
uv run agents check
```

[`_template/README.md`](./_template/README.md) walks through it, including
how to bring your own dependencies instead of staying standard-library only.
Read [`AGENT_PROTOCOL.md`](./AGENT_PROTOCOL.md) before writing an agent from
scratch rather than copying.

The agent does not import the orchestrator, and the orchestrator does not
import the agent. An agent folder should still work if copied out of this
repo — `realty-lead-gen` answers a request piped straight into
`uv run python -m realty_lead_gen.agentcall` with nothing else present.

## What the orchestrator does not do yet

Discovery, one transport, and a CLI. Everything past that is unbuilt on
purpose, because with one agent the design would be a guess:

- **Routing.** Choosing an agent by capability rather than by name.
- **Composition.** Chaining agents, fan-out, retries across agents. The
  protocol keeps agents as leaves so composition can live here later.
- **Cost aggregation.** Every envelope carries `usage`; nothing sums it yet.
- **Other transports.** The envelope is transport-agnostic; HTTP or a queue
  would touch only `runner.py`.
- **Registry drift check.** `agents check` catches a bad manifest, but
  nothing verifies the README table against the registry.

## CI

All workflows live in `.github/workflows/` at the root, because GitHub
Actions does not discover workflows nested inside a directory. Each agent's
pipeline is scoped with a `paths:` filter so it runs when that agent changes
and not otherwise.

| Workflow | Runs | Gate |
|---|---|---|
| `orchestrator.yml` | every change | registry validation + contract and template tests (no agent dependencies), then `agents check` against every registered agent |
| `realty-lead-gen.yml` | `realty-lead-gen/**` | ruff, mypy strict, unit tests, integration against real Postgres and Redis, combined coverage gate |

`agents check` is the integration gate worth understanding: it calls
`describe` on every registered agent, which costs nothing and no credentials,
but catches a registry that has drifted from disk, a broken entrypoint, and a
manifest that no longer matches its agent's code.

`realty-lead-gen`'s pipeline used to sit in its own folder, where it was
never discovered and therefore never ran. A repository with no red builds
looks like a repository with passing builds, which is the more dangerous
failure. The root copy is now authoritative; delete
`realty-lead-gen/.github/` once it is in place, so two copies of a 120-line
pipeline cannot drift.

Not yet linted: the orchestrator's own Python. `realty-lead-gen` runs ruff
and strict mypy on itself, and root code should hold the same bar.

## Licensing

No repository-wide license. Licensing is per agent: each agent folder
carries its own `LICENSE`, and `realty-lead-gen/LICENSE` is proprietary. A
new agent should ship an explicit `LICENSE` rather than inherit an
assumption.
