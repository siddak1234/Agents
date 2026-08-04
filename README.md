# Agents

A repository of independent agents and one orchestrator that calls them.

An agent here is something you **call**, not something you boot: one JSON
request in, one structured result out, with cost attached. The contract is
[`docs/AGENT_PROTOCOL.md`](./docs/AGENT_PROTOCOL.md) and it is the only thing the
orchestrator knows about any agent.

Every agent is independent. It lives in its own folder, brings its own
dependencies, and is called in its own process — so one agent cannot break
another, and any agent still works if you copy its folder out. That isolation
is the point of the repository, and the contract is what buys it.

> ### 👉 Here to build an agent? Start with [`docs/INTERN_BRIEF.md`](./docs/INTERN_BRIEF.md)
>
> One page, start to finish. You need **no access to this repository** — you
> fork it — and you need git plus [`uv`](https://docs.astral.sh/uv/). Nothing
> else is required.
>
> **Two ways to build, both fully supported:**
>
> - **With Claude Code** — `/new-agent` interviews you and scaffolds; `/raise-pr`
>   runs the gates, puts your branch in front of four reviewers, and opens the
>   pull request.
> - **With any chat assistant, outside an IDE** — ChatGPT, Gemini, Claude.ai,
>   whatever you have. The brief is written for this and carries the whole
>   contract inside it, so a chat that has never seen this repository can still
>   help you build a correct agent. Part 2 is an interview prompt you paste in;
>   Part 6 is the contract.
>
> Same standard either way — the gates and the review do not care which you used.
>
> [Adding an agent](#adding-an-agent) below is the same path in nine steps, if
> you prefer the summary first.

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
| [`operations-maintenance-agent`](./agents/operations-maintenance-agent) | active | `describe`, `generate_maintenance_plan` |

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

Working from a fork? Use `--base upstream/main` — see "Adding an agent".

Every call runs the agent as a subprocess **in its own folder, with its own
environment**. That is what makes an agent's relative paths resolve, keeps one
agent's dependencies from breaking another's, and stops an agent seeing
credentials it never declared.

## Adding an agent

You need **no access to this repository** — fork it. You need **no coding
tool**: git, [`uv`](https://docs.astral.sh/uv/), a text editor and any free
chat assistant are enough. The steps below are the whole path, in order.
[`docs/INTERN_BRIEF.md`](./docs/INTERN_BRIEF.md) is the same path with the
contract attached; open it at step 4 and keep it open.

**1. Fork.** Press Fork on this page. Your fork is yours — you have write
access to it and none to this one.

**2. Clone it, and point `upstream` here.**

```bash
git clone https://github.com/<you>/Agents.git
cd Agents
git remote add upstream https://github.com/siddak1234/Agents.git
git fetch upstream
uv sync --frozen
uv run agents list          # prints the agents that already exist
```

`upstream` is not optional. Your `origin/main` freezes at the moment you
forked, so every check that compares against "main" must use `upstream/main`
or it judges your work against a stale snapshot.

**3. Decide what the agent is** — before any code.
[`INTERN_BRIEF.md` Part 2](./docs/INTERN_BRIEF.md) carries an interview prompt:
paste it into your chat assistant and it asks you eight questions one at a
time, pushing back while an answer is still vague. That is the highest-value
step here — most of what gets a pull request sent back is decided in it. Read
the `agents list` output first: if an existing agent owns this ground, the right
contribution is a capability on that agent, not a new one.

**4. Scaffold, then branch.**

```bash
uv run agents new parcel-geo     # your name, lowercase-with-hyphens
git checkout -b parcel-geo
```

`agents new` copies `agents/_template/`, sets the name in the two files that
must agree, registers it in `registry.yaml`, adds the README row, and prints
the five things it deliberately did **not** do. Leave your fork's `main` alone
as a mirror of `upstream/main` — work on the branch, or your second agent's
pull request will carry your first.

**5. Build it.** Paste [`INTERN_BRIEF.md` Part 6](./docs/INTERN_BRIEF.md) into
any chat assistant, with your Part 2 answers. Part 6 is self-contained — wire
format, the five error types, the six rules, `agent.yaml`, a working skeleton,
and a ready-made prompt. Save the files it returns into `agents/parcel-geo/`.

**6. Work the to-do list.** A fresh scaffold fails on purpose. `uv run agents
list --strict` names every reason:

```
error: parcel-geo: no LICENSE...
error: parcel-geo: still has `TODO(new agent)` markers in README.md, agent.yaml,
       agent_main.py, tests/test_agent.py...
error: parcel-geo/agent.yaml: description is still the template's...
error: parcel-geo/agent.yaml: offers only the template's example capabilities...
```

Those four are the difference between a renamed copy and an agent.

**7. Prove it.**

```bash
uv run agents verify                        # must print: All 10 gates pass
git fetch upstream
uv run agents scope --base upstream/main    # must print: ok    scope: parcel-geo
```

`verify` is the ten gates CI runs; its output is your to-do list until it is
clean. `scope` checks you changed only your own agent.

**8. Self-review.** [`INTERN_BRIEF.md` Part 5](./docs/INTERN_BRIEF.md) has a
nine-item list — declared capabilities matching the code, real schemas,
`runtime.env.inherit` no wider than you read, no business logic in the
entrypoint. On a fork this is the first review your work gets, because the
model-driven board cannot run there: GitHub withholds secrets from fork pull
requests.

**9. Push to your fork and open the pull request.**

```bash
git add -A && git commit -m "Add parcel-geo"
git push -u origin parcel-geo
```

Then open it on GitHub, from your branch to `siddak1234/Agents` `main`, and
fill in the template. The deterministic checks run on your pull request — they
need no credentials, so a fork gets them in full. A maintainer reads the diff
and approves; you cannot merge it yourself and cannot push to `main`.

Two examples exist on purpose: `agents/_template/` is the skeleton,
`agents/realty-lead-gen/` is a worked reference that calls a model — structured
output, graceful degradation, its own locked dependencies.
[`docs/CONTRIBUTING.md`](./docs/CONTRIBUTING.md) is the same contract written
for someone who already has the repository open.

## Development

```bash
uv run agents verify        # every deterministic gate CI runs, in one command
uv run agents scope --base origin/main   # the one it cannot: your branch vs main
uv run agents scope --base origin/main --allow-platform   # …if it is platform work
uv run pre-commit install   # optional: most of the same gates, per commit
```

`scope` refuses a branch that changes shared code, including one that changes
nothing else — that is a maintainer's pull request, and `--allow-platform`
says so. The flag is inert once an agent folder is touched.

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
