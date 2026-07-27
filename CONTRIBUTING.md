# Contributing an agent

This repo holds many independent agents and one orchestrator that calls them.
Your agent can do anything. It must be callable the same way everything else
here is.

Read [`AGENT_PROTOCOL.md`](./AGENT_PROTOCOL.md) first — it is the contract,
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
| [`realty-lead-gen/`](./realty-lead-gen) | A worked reference. Real dependencies, database, migrations, its own CI. What a mature agent looks like. |

Copy the template. Read the reference when you need to see how something is
done at scale.

## Adding your agent

```
/new-agent a weather forecast agent for field crews
```

That interviews you first — why this should exist, who calls it, what the
capabilities are, what goes in and out of each, what can go wrong, what
credentials it needs — and only then scaffolds, writing your answers into
`agent.yaml`. Most of what the review board later blocks on is decided in that
conversation rather than in code.

By hand instead:

```bash
cp -r _template my-agent && cd my-agent
```

Everything needing a change is marked `TODO(new agent)`.

1. **Rename.** `name:` in `agent.yaml` and `AGENT_NAME` in `agent_main.py`
   must both equal the folder name.
2. **Implement.** Replace the example capability. Keep `describe`.
3. **Declare.** Every capability in `agent.yaml`, with input and output
   schemas, plus any environment variables under `runtime.env.inherit`.
4. **Register.** Add `- path: my-agent` to `registry.yaml` and a row to the
   README table.
5. **Verify.**

   ```bash
   uv run agents list --strict    # is it actually integrated?
   uv run agents check my-agent   # does it run and match its manifest?
   uv run agents call my-agent <capability> --input '{...}'
   uv run pytest
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
| tests | yes | Placement is yours — `tests/` by convention |
| `CLAUDE.md` | if it has house rules | Conventions specific to your agent |
| dependency manifest | if you have dependencies | `pyproject.toml` or equivalent |
| `.gitignore` | if your toolchain needs one | Agent-specific ignores only |

**The template ships no `LICENSE` on purpose.** One that did would have you
inherit a licence by accident, which is the thing per-agent licensing exists
to prevent. `--strict` will tell you to add it.

Test placement is not enforced, because it cannot be without assuming your
language. Note that your tests run in CI only once your agent has its own
workflow — `.github/workflows/realty-lead-gen.yml` is the worked example.
Until then they are yours to run.

Your folder should still make sense if someone copied it out of this repo and
ran it alone. Depend on the orchestrator for nothing.

## Non-negotiable rules

These are enforced by tests and reviewed on every PR.

1. **stdout carries the envelope and nothing else.** Logs go to stderr. Point
   `sys.stdout` at stderr before doing any work — this is usually broken by a
   dependency printing, not by your own code.
2. **Exit 0 whenever an envelope was produced**, including failures. A
   business failure is a successful call that returned a failure.
3. **Validate your own input.** The schemas in `agent.yaml` are documentation;
   the orchestrator does not enforce them.
4. **A missing credential returns `unavailable`, never a crash.**
5. **Declare the environment you need — nothing more.** You receive only what
   `runtime.env.inherit` names. Do not ask for a variable a capability does
   not use.
6. **`describe` must not import heavy dependencies.** It has to answer on a
   machine that cannot run the rest of you.

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

The same four reviewers run again in CI on the pull request, and *that* run is
what branch protection enforces. `/raise-pr` is the fast path — a minute
instead of a push-and-wait — not the gate.

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
