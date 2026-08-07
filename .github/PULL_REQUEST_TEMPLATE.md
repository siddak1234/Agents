## What this changes

<!-- One or two sentences. If it adds an agent, say what the agent does. -->

## Checklist

Delete the section that does not apply.

### Adding or changing an agent

- [ ] `uv run agents verify <your-agent>` prints `All 10 gates pass` — every
      deterministic gate CI runs on the working tree, including the secret scan
      and your agent's own declared `runtime.lint` and `runtime.test` commands.
      Name your agent: unnamed, it also lints and tests every other registered
      agent, and their dependencies are not yours to build
- [ ] `uv run agents scope --base origin/main` passes — the one deterministic
      gate `verify` cannot run for you, because it compares against a base.
      (The review board is the only other CI check, and it needs a token the
      repository owner configures.)
- [ ] Folder name, `name:` in `agent.yaml`, and `AGENT_NAME` all match
- [ ] Description and capabilities are this agent's, not the template's
- [ ] No `TODO(new agent)` markers left anywhere in the folder — the gate
      scans every file, not just `agent.yaml`
- [ ] Every capability in the code is declared in `agent.yaml`, and vice versa
- [ ] `runtime.env.inherit` names only variables a capability actually uses
- [ ] `runtime.lint` runs a real linter — the template ships `compileall` as a
      placeholder and says to replace it once you have dependencies
- [ ] No test reaches the network, including with a fake API key set
- [ ] Registered in `registry.yaml` and listed in the README table
- [ ] Agent has its own `README.md`, `LICENSE`, and tests
- [ ] Runs standalone — the folder does not depend on the orchestrator
- [ ] A missing credential returns `unavailable` rather than crashing
- [ ] Nothing but the envelope is written to stdout

#### If your agent calls a model, also

Question 7 of the brief asks for three things: structured output through a tool
schema, graceful degradation without a key, and usage reported. Only the middle
one was ever on this list — it is the credential box above — and the other two
reached review missing twice on the same pull request. The last box here is not
from the brief; it is what review found underneath them.

- [ ] Structured output comes from a tool schema with `tool_choice`, not from
      `json.loads` on the reply text — a fenced or chatty reply must not be
      able to turn a good answer into a failure
- [ ] The output is checked against the types `agent.yaml` declares, not just
      for the presence of its keys
- [ ] `usage` carries real `input_tokens`/`output_tokens` **and a computed
      `cost_micros`** — including on a failure that happened after the call,
      where the tokens were already billed
- [ ] A model that returns something unusable is not reported as
      `invalid_request` — the caller's request was fine

### Changing the orchestrator or the contract

- [ ] `uv run agents scope --base origin/main --allow-platform` passes — the
      flag is how a maintainer declares platform work, and it only covers a
      branch with no agent in it
- [ ] A test fails without this change
- [ ] `docs/AGENT_PROTOCOL.md` updated if the wire format or guarantees moved
- [ ] No agent-specific behaviour added to shared code
- [ ] Existing agents still pass `agents check`

## Anything reviewers should know

<!-- Trade-offs, things you deliberately did not do, follow-ups. -->
