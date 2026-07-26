# Template agent

Copy this folder to start a new agent. It is a complete, working
`agentcall/v1` agent in about 100 lines of standard-library Python — no
install step, nothing to configure.

Run it right now, without the orchestrator:

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

That an agent answers with the orchestrator uninvolved is the point, not a
convenience. Agents do not depend on the orchestrator; the orchestrator
depends on agents.

## Making it yours

```bash
cp -r _template my-agent
cd my-agent
```

Then, in order:

1. **`agent.yaml`** — set `name` to `my-agent`. It must equal the folder name
   or discovery rejects it.
2. **`agent_main.py`** — set `AGENT_NAME` to match, replace `greet` with your
   real capability, keep `describe`.
3. **`agent.yaml` capabilities** — declare what you added, with schemas.
4. **Root `registry.yaml`** — add `- path: my-agent`.
5. **Root `README.md`** — add a row to the agents table.
6. Verify:

   ```bash
   cd .. && uv run agents check && uv run agents call my-agent greet --input '{"name":"you"}'
   ```

Search the folder for `TODO(new agent)` — every spot needing a change is
marked.

## Bringing your own dependencies

This template is dependency-free so it runs anywhere. A real agent normally
is not. Add a `pyproject.toml`, then point the manifest at your environment:

```yaml
runtime:
  type: subprocess
  command: ["uv", "run", "python", "-m", "my_agent.agentcall"]
```

The orchestrator runs that command with the working directory set to your
folder, so `uv` finds your project and your relative paths resolve. Your
dependencies stay yours — they are never resolved against the orchestrator's
or another agent's. See `realty-lead-gen/` for a full example.

Nothing requires Python. Any language that can read stdin and write JSON to
stdout can be an agent; only `runtime.command` changes.

## The four rules

Marked `RULE` in `agent_main.py`, spelled out in `AGENT_PROTOCOL.md`:

1. **stdout carries the envelope and nothing else.** Point `sys.stdout` at
   stderr first. This is usually broken by a dependency printing, not by your
   own code — `realty-lead-gen` configures structlog to stdout, and one log
   line makes the envelope unparseable.
2. **Exit 0 whenever an envelope was produced**, including for `ok:false`.
3. **Validate your own input** and say precisely what was wrong.
4. **A missing credential returns `unavailable`, never a crash.**

## Why this folder is not in the registry

`_template` is not listed in `registry.yaml`, so it never appears in
`agents list` as though it were real. `tests/test_template.py` still runs it
on every CI build — a template that quietly stopped working would be worse
than no template at all.
