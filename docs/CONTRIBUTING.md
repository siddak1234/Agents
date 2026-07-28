# Contributing an agent

This repo holds many independent agents and one orchestrator that calls them.
Your agent can do anything. It must be callable the same way everything else
here is.

Read [`docs/AGENT_PROTOCOL.md`](./AGENT_PROTOCOL.md) first — it is the contract,
and it is short.

## The mental model

**An agent is called, not booted.** One JSON request in, one structured
result out, with cost attached. It is not a service you start and keep
running; if yours also happens to run as a web service, that is a deployment
detail, not what makes it an agent.

Two examples, deliberately at opposite ends:

| | What it shows |
|---|---|
| [`_template/`](./_template) | The skeleton. ~100 lines, standard library, runs with no install. **Start here.** |
| [`realty-lead-gen/`](./agents/realty-lead-gen) | A worked reference: a model-calling agent with real dependencies — structured output through a tool schema, graceful degradation without a key, integer-cents money. Eleven modules; the service it grew out of lives in its own repository. |

Copy the template. Read the reference when you need to see how something is
done at scale.

## How big is an agent?

Small. The contract costs four files — manifest, entrypoint, tests, README —
and a finished agent is usually that plus a module or two of real work. This
is Anthropic's guidance for building on Claude, not just house taste: start
with the simplest thing that meets the need, keep the tool surface small and
well-described, add machinery only when a capability demands it.

The rule is about **shape, not line count** (a line count is a proxy this
repository refuses to use). An agent that needs any of the following is a
*service wearing an agent's manifest*, and the board blocks it:

- a web server or open port — an agent is called, not booted
- a database it owns, with migrations
- Docker, docker-compose, or any "start this first" step
- a background worker or job queue

Needing one of these is not a sin — it means you are building a service, and
a service belongs in its own repository, exposing an `agentcall/v1` adapter
here only if something actually calls it. If a *capability* genuinely
requires heavy machinery, the burden is on your README to justify it
per capability, and "the data has to live somewhere" is not a justification —
that is the caller's problem, or a service's.

## Adding your agent

**Not using Claude Code?** Read [`docs/INTERN_BRIEF.md`](./docs/INTERN_BRIEF.md)
instead of this section. It is self-contained and written to be pasted into a
Claude conversation, which is what gives a chat assistant any idea what
`agentcall/v1` is — without it, it will invent something plausible and wrong.

In Claude Code:

```
/new-agent a weather forecast agent for field crews
```

That interviews you first — why this should exist, who calls it, what the
capabilities are, what goes in and out of each, what can go wrong, what
credentials it needs — and only then scaffolds, writing your answers into
`agent.yaml`. Most of what the review board later blocks on is decided in that
conversation rather than in code.

Anywhere else:

```bash
uv run agents new my-agent
```

That does the mechanical half — copies the template, sets the name in the two
files that must agree, registers it, adds the README row. It deliberately
leaves the description, the capabilities, the `TODO(new agent)` markers and
the missing `LICENSE` alone: those are the decisions that make it an agent
rather than a copy, and `--strict` reporting them is the integration.

1. **Rename.** `name:` in `agent.yaml` and `AGENT_NAME` in `agent_main.py`
   must both equal the folder name.
2. **Implement.** Replace the example capability. Keep `describe`.
3. **Declare.** Every capability in `agent.yaml`, with input and output
   schemas, plus any environment variables under `runtime.env.inherit`.
4. **Register.** Add `- path: agents/my-agent` to `registry.yaml` and a row to the
   README table.
5. **Verify.**

   ```bash
   uv run agents verify           # every deterministic gate CI runs, at once
   ```

   Or individually, when you want one answer rather than all of them:

   ```bash
   uv run agents list --strict    # is it actually integrated?
   uv run agents check my-agent   # does it run and match its manifest?
   uv run agents test my-agent    # does its own test command pass?
   uv run agents lint my-agent    # does its own lint command pass?
   uv run agents call my-agent <capability> --input '{...}'
   ```

   `--strict` is the one to run first. A renamed copy of the template passes
   everything else — it loads, it answers, its manifest is valid — while still
   describing itself as a template and offering only the example capability.
   `--strict` is what tells *registered* from *integrated*, and working
   through what it reports is the integration.

## What your folder must contain

| File | Required | Purpose |
|---|---|---|
| `agent.yaml` | yes | Manifest — how to run you, what you offer, what you may read |
| entrypoint | yes | Implements `agentcall/v1` on stdin/stdout |
| `README.md` | yes, enforced | What it does, how to run it, how to configure it |
| `LICENSE` | yes, enforced | Licensing is per agent; state it explicitly |
| tests | yes, enforced | Placement is yours; declare `runtime.test` so CI runs them |
| lint config | yes, enforced | Declare `runtime.lint`; root tooling checks root-owned code only |
| `CLAUDE.md` | if it has house rules | Conventions specific to your agent |
| dependency manifest | if you have dependencies | `pyproject.toml` or equivalent |
| `.gitignore` | if your toolchain needs one | Agent-specific ignores only |

**The template ships no `LICENSE` on purpose.** One that did would have you
inherit a licence by accident, which is the thing per-agent licensing exists
to prevent. `--strict` will tell you to add it.

Test placement is not enforced — that cannot be done without assuming your
language. Instead your manifest declares `runtime.test`, the command that runs
them from your folder, and CI runs it for every agent a pull request touches.
An agent that declares none fails `--strict`: tests nothing can run are tests
nobody runs.

Your folder should still make sense if someone copied it out of this repo and
ran it alone. Depend on the orchestrator for nothing.

## Non-negotiable rules

The rules live in one place: [`docs/AGENT_PROTOCOL.md` §"Rules an agent must
follow"](./AGENT_PROTOCOL.md#rules-an-agent-must-follow), plus the
environment rule in its `agent.yaml` section. This file used to carry its own
copy with its own numbering; the two drifted, and "rule 5" meant different
things depending on which file you had open. Cite the protocol's numbers.

What enforces them: the transport tests pin the stdout rule, the exit-0 rule
and the deny-by-default environment. The rest — input validation, graceful
`unavailable`, a light `describe` — are exactly what the review board reads
your diff for, which is why a reviewer's finding on them is blocking.

## Opening the PR

Small and complete beats large and staged. One agent per PR.

```
/raise-pr Add weather-agent
```

That runs the deterministic gates, then puts your branch in front of the
**review board** — four reviewers reading your diff in parallel. If none of
them blocks, it opens the pull request and carries their advisory notes into
the description. If any blocks, no pull request is opened and you get the
findings with the file and the fix.

`/raise-pr` runs in your own Claude Code session, on your own subscription —
there is nothing to configure and no key involved.

The same four reviewers run again in CI on the pull request. That run becomes
the gate only once the repository owner configures two things: a
`CLAUDE_CODE_OAUTH_TOKEN` secret so the board can authenticate, and branch
protection requiring the **review board** check. Until then it is advisory —
and on a fork pull request it cannot run at all, because GitHub withholds
secrets from forks; a human reads those diffs. `/raise-pr` is the fast path —
a minute instead of a push-and-wait — not the gate.

CI builds and describes **your agent only**, so a red build is about your work
and nobody else's. The exception is a change under `orchestrator/`: shared
code can break every agent, so CI sweeps them all and your PR needs a test
that fails without your change.

## The review board

Reviewers are **not** agents. They live in `.claude/agents/`, run inside
Claude Code, and review this repository rather than doing work for a user.

| Reviewer | Asks |
|---|---|
| `agent-architect` | Is this a well-formed agent — clear purpose, description someone could choose by, coherent capabilities, contract honoured? |
| `solution-architect` | Does it belong here, shaped this way? Should it be a capability of an existing agent instead? |
| `engineer-reviewer` | Is the code correct, and would the tests catch a regression? |
| `anthropic-practice` | Does it follow Anthropic's practice for agents built on Claude? |

A finding is **blocking** only when it names a real defect — an input that
breaks it, a leaked credential, a structural choice expensive to undo.
Everything else is advisory and never fails a build. A board that blocks on
taste gets ignored, and then it blocks on nothing.

Two things reviewers will not do: fix your code, or debate their own findings.
You revise and run `/raise-pr` again.

## What the board pushes back on

- An agent that needs a server started before it can be called.
- Service shape in general: an owned database, migrations, Docker, a worker
  queue — see "How big is an agent?". Blocking unless a capability justifies
  it in the README.
- A description that restates the name, or survives from the template.
- Capabilities in code but not in `agent.yaml`, or the reverse.
- `runtime.env.inherit` broader than the capabilities justify.
- Business logic in the agentcall adapter — it translates the wire protocol
  and nothing else.
- A file without one reason to change. Judged by cohesion, not line count.
- Tests that assert the implementation back at itself.
- Anything added to `orchestrator/` for one agent's benefit. Cross-agent
  features need two agents to design against — say so instead of guessing.
- Documentation that describes intent rather than what the code does.
