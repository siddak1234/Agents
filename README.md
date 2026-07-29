# Agents

A repository of independent agents and one orchestrator that calls them.

An agent here is something you **call**, not something you boot: one JSON
request in, one structured result out, with cost attached. The contract is
[`docs/AGENT_PROTOCOL.md`](./docs/AGENT_PROTOCOL.md) and it is the only thing the
orchestrator knows about any agent.

## Layout

Three folders, and building an agent needs only two of them:

```
agents/              every agent, one folder each — yours goes here
  _template/           a working agent to copy. Start here.
  realty-lead-gen/     a worked reference
docs/                the contract, how to contribute, the intern brief
orchestrator/        the platform that calls agents, and its own tests
```

Plus `registry.yaml` (which agents exist — paths only) and `.claude/`
(editor guidance). Everything else at the root is there because a tool
insists on finding it there: `pyproject.toml`, `uv.lock`,
`.pre-commit-config.yaml`, `.gitignore`.

## Agents

| Agent | Status | Capabilities |
|---|---|---|
| [`realty-lead-gen`](./agents/realty-lead-gen) | active | `grade_photos` |

This table is maintained by hand (and by `agents new`, which adds a row).
What keeps it honest is `agents list --strict`, which fails when a registered
agent has no row here — it cannot catch a stale Status cell, so treat that
column as prose, not telemetry. For machine-readable output — which is what
CI reads to work out its scope — use `uv run agents list --json`.

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
uv run agents verify                          # every deterministic gate CI runs
uv run agents scope --base origin/main        # …except this one: it needs a base
```

Every call runs the agent as a subprocess **in its own folder, with its own
environment**. That is what makes an agent's relative paths resolve, keeps one
agent's dependencies from breaking another's, and stops an agent seeing
credentials it never declared.

## Adding an agent

`uv run agents new my-agent`, then follow
[`docs/CONTRIBUTING.md`](./docs/CONTRIBUTING.md). Building from claude.ai chat instead
of Claude Code? [`docs/INTERN_BRIEF.md`](./docs/INTERN_BRIEF.md) is the
self-contained version — paste it into a chat and it carries the whole
contract with it. Two examples exist on purpose: `agents/_template/` is
the skeleton, `agents/realty-lead-gen/` is a worked reference that calls a
model —
structured output, graceful degradation, its own locked dependencies.

## Development

```bash
uv run agents verify        # every deterministic gate CI runs, in one command
uv run agents scope --base origin/main   # the one it cannot: your branch vs main
uv run pre-commit install   # optional: most of the same gates, per commit
```

`agents verify` is the contributor-facing definition of the gates — when a
gate is added it belongs there and in the CI workflows, never only in a doc.
The one CI check beyond it is the review board, which is model-driven and
needs a token. The pre-commit hooks cover the fast subset (format, lint, types,
secret scan, registry checks); the full test suites run in `verify` and CI.

Root tooling covers root-owned code only. Each agent lints and tests itself
from its own folder, with its own configuration — the same isolation that
applies to dependencies.

## CI

Workflows live in `.github/workflows/` at the root, because GitHub Actions
does not discover workflows nested inside a directory. Both run on every pull
request; the per-agent work inside them is scoped at runtime by diffing
against the base, not by a `paths:` filter.

| Workflow | Runs on | Gate |
|---|---|---|
| `orchestrator.yml` | every change | secret scan, ruff, strict mypy, registry validation, contract and template tests; then, per agent in scope: `agents check`, `agents lint`, `agents test` |
| `review.yml` | pull requests | the deterministic gates, then the review board — four reviewers reading the diff. Authenticates with `CLAUDE_CODE_OAUTH_TOKEN` (your Claude subscription, via `claude setup-token`) or `ANTHROPIC_API_KEY`; warns and skips with neither. Advisory until it is required in branch protection — and fork pull requests never receive secrets, so on a fork the board cannot run at all and a human reads the diff |

`agents check` calls `describe` on an agent — no network, no cost, and the
handshake runs with inherited credentials withheld — and compares what the
agent reports against its manifest. A capability declared and not
implemented, or implemented and not declared, fails here. `agents lint` and
`agents test` then run the agent's own declared commands in its own folder,
so a contributed agent's source and tests are actually checked.

**The agent steps are scoped to what changed.** A pull request adding one
agent builds and describes that agent only; it does not build every other
agent, and it cannot go red because someone else's agent is broken. Changing
`orchestrator/` or the root project does sweep every agent, because shared
code can break any of them. A root documentation-only change still runs the
cheap contract job, but every agent step is skipped; documentation inside an
agent's folder triggers that agent's own pipeline, because its coverage gate
cannot know the change was prose.

Registry integrity is checked separately and statically by
`agents list --strict`, which costs nothing and catches the mistake new
contributors actually make: writing an agent and forgetting to register it.
Discovery ignores unregistered folders by design, so without that check the
agent would merge green and simply never be callable.

## Roadmap

[`docs/ROADMAP.md`](./docs/ROADMAP.md) is where this repository is going —
the contribution funnel, the guidance surface, multi-tenancy, runtime,
evaluation, certification alignment, and what is deliberately not being
built yet.

## How the repository guides you

Five mechanisms, each doing what only it can:

| | Does |
|---|---|
| **Skills** (`.claude/skills/`) | `/new-agent` interviews you before scaffolding, so the purpose exists before the folder does |
| **Commands** (`.claude/commands/`) | `/raise-pr` runs the gates, then the board, then opens the PR — user-invoked only, which is what raising a PR should be |
| **Rules** (`.claude/rules/`) | Load automatically when you edit a manifest, an entrypoint, or shared code — contract reminders at the moment they apply |
| **Hooks** (`.claude/settings.json`) | Edit a manifest and the integration gate runs; a half-finished agent is reported before you get further |
| **Tools** (`agents` CLI) | `list`, `describe`, `call`, `check`, `test`, `lint`, `new`, `verify`, `scope` — the same commands CI runs |

Rules guide, hooks enforce. A rule is context you may act on; a hook exits
non-zero and has to be dealt with.

## Licensing

No repository-wide license. Each agent folder carries its own;
`agents/realty-lead-gen/LICENSE` is proprietary. A new agent should ship an explicit
`LICENSE` rather than inherit an assumption.
