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
├── orchestrator/       discovery, manifest loading, transport, CLI
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

1. Create a folder at the repository root, `kebab-case`. It must not collide
   with a reserved root name — see [`CLAUDE.md`](./CLAUDE.md).
2. Give it everything it needs to stand alone: its own dependency manifest,
   tests, `README.md`, `CLAUDE.md`, and `.gitignore`.
3. Implement [`agentcall/v1`](./AGENT_PROTOCOL.md) — read one JSON request
   from stdin, write one envelope to stdout, logs on stderr.
4. Write an `agent.yaml` in the folder declaring the run command and every
   capability, including `describe`.
5. Add its path to `registry.yaml` and a row to the table above.
6. Run `uv run agents check`.

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

There is still no root-level CI. In a monorepo each agent needs a workflow
scoped with a `paths:` filter, or its pipeline either never runs or runs on
every commit. `realty-lead-gen/.github/workflows/ci.yml` is a complete
pipeline but sits one level down, where GitHub Actions does not discover it,
so it is **dormant**. `agents check` is the natural first root-level job.

## Licensing

No repository-wide license. Licensing is per agent: each agent folder
carries its own `LICENSE`, and `realty-lead-gen/LICENSE` is proprietary. A
new agent should ship an explicit `LICENSE` rather than inherit an
assumption.
