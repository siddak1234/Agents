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

CI builds and describes **your agent only** — not everyone else's — so the
result is about your work and nothing else. Two checks are worth knowing
before you push:

- `agents list --strict` fails if your folder has an `agent.yaml` but is
  missing from `registry.yaml`. Forgetting step 4 is the most common way a
  new agent lands and is never callable.
- `agents check <your-agent>` runs your entrypoint and compares what it
  reports against your manifest. A capability in one but not the other fails.

If you touched anything under `orchestrator/`, CI sweeps every agent — shared
code can break all of them — and your PR needs a test that fails without your
change. Fill in the checklist in the PR template.

## What reviewers push back on

- An agent that needs a server started before it can be called.
- Capabilities that exist in code but not in `agent.yaml`, or vice versa.
- `runtime.env.inherit` broader than the capabilities justify.
- Business logic in the agentcall adapter. It translates the wire protocol and
  nothing else.
- Anything added to `orchestrator/` for one agent's benefit. Cross-agent
  features need two agents to design against — say so instead of guessing.
- Documentation that describes intent rather than what the code does.
