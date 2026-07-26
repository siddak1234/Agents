# Template agent

A complete, working `agentcall/v1` agent in ~100 lines of standard-library
Python. No install step, nothing to configure. Copy this folder to start.

Run it now, without the orchestrator:

```bash
echo '{"protocol":"agentcall/v1","capability":"greet","input":{"name":"Dak"}}' \
  | python3 agent_main.py
```

```json
{"protocol": "agentcall/v1", "ok": true, "capability": "greet",
 "output": {"greeting": "Hello, Dak!"},
 "usage": {"input_tokens": 0, "output_tokens": 0, "cost_micros": 0},
 "error": null}
```

That it answers with the orchestrator uninvolved is the point, not a
convenience. Agents do not depend on the orchestrator.

## Making it yours

```bash
cp -r _template my-agent
```

Search the copy for `TODO(new agent)` — every spot needing a change is
marked. [`CONTRIBUTING.md`](../CONTRIBUTING.md) has the full checklist and
the rules reviewers apply; `agent_main.py` marks the four contract rules
inline as `RULE`, at the line where each is easy to break.

## Bringing your own dependencies

This template is dependency-free so it runs anywhere. A real agent usually is
not. Add a `pyproject.toml`, then point the manifest at your environment:

```yaml
runtime:
  type: subprocess
  command: ["uv", "run", "python", "-m", "my_agent.agentcall"]
```

The orchestrator runs that command with the working directory set to your
folder, so `uv` finds your project and your relative paths resolve. Your
dependencies are never resolved against the orchestrator's or another
agent's. `realty-lead-gen/` is the worked example.

Nothing requires Python. Any language that can read stdin and write JSON to
stdout can be an agent; only `runtime.command` changes.

## Why this folder is not in the registry

It is absent from `registry.yaml`, so it never appears in `agents list` as
though it were real. `tests/test_template.py` still runs it on every build —
including copying it and applying the renames above — because a template that
quietly stopped working is worse than none, given copying it is the first
thing anyone does.
