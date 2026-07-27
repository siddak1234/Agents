## What this changes

<!-- One or two sentences. If it adds an agent, say what the agent does. -->

## Checklist

Delete the section that does not apply.

### Adding or changing an agent

- [ ] `uv run agents check` passes
- [ ] `uv run pytest` passes
- [ ] Folder name, `name:` in `agent.yaml`, and `AGENT_NAME` all match
- [ ] Every capability in the code is declared in `agent.yaml`, and vice versa
- [ ] `runtime.env.inherit` names only variables a capability actually uses
- [ ] Registered in `registry.yaml` and listed in the README table
- [ ] Agent has its own `README.md`, `LICENSE`, and tests
- [ ] Runs standalone — the folder does not depend on the orchestrator
- [ ] A missing credential returns `unavailable` rather than crashing
- [ ] Nothing but the envelope is written to stdout

### Changing the orchestrator or the contract

- [ ] A test fails without this change
- [ ] `AGENT_PROTOCOL.md` updated if the wire format or guarantees moved
- [ ] No agent-specific behaviour added to shared code
- [ ] Existing agents still pass `agents check`

## Anything reviewers should know

<!-- Trade-offs, things you deliberately did not do, follow-ups. -->
