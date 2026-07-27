---
paths:
  - "orchestrator/**"
  - "tests/**"
  - "registry.yaml"
  - "AGENT_PROTOCOL.md"
---

# Editing shared code

Everything here is depended on by every agent. A change is a change to all of
them.

**The orchestrator never imports agent code, and no agent imports the
orchestrator.** The first keeps one agent's dependencies from breaking
everything else; the second keeps an agent working when extracted. Both
directions are tested — if a change needs either, the change is wrong.

**One runtime dependency: `pyyaml`.** Anything added here is paid for by every
agent, forever. Reach for the standard library.

**Nothing goes in for one agent's benefit.** Cross-agent features need two
agents to design against. With one, you are guessing — say so instead.

**A change needs a test that fails without it.** These files have no product
behaviour of their own; the tests are the specification.

**Root tooling covers root-owned code only** — `orchestrator`, `tests`,
`_template`. Agents lint and type-check themselves with their own
configuration. Commands pass explicit paths, never `.`.

**Changing `AGENT_PROTOCOL.md` changes a contract other people implement.**
Say what existing agents must do, and whether they must do it now.

Verify with:

```bash
uv run pytest -q && uv run mypy orchestrator && uv run agents check
```
