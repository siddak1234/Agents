# Building your agent

You are going to add one agent to this repository. This page is everything
you need, and it is written to be **pasted into a Claude conversation** as
your first message, followed by what you want your agent to do.

If you are using Claude Code instead, you do not need this — run
`/new-agent` and it will interview you.

---

## Part 1 — Set up (once, about two minutes)

You need `git` and `uv`. Nothing else — `uv` installs the right Python
version itself.

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then fork this repository on GitHub, and:

```bash
git clone https://github.com/<you>/Agents.git
cd Agents
uv sync --frozen
uv run agents list          # should print the agents that already exist
```

If that last command prints a list, you are ready.

## Part 2 — Create your folder

```bash
uv run agents new my-agent    # use your agent's real name, lowercase-with-hyphens
git checkout -b my-agent
```

That copies the template, sets the name in the two files that must agree,
registers it, and adds it to the README table. It deliberately does **not**
write your description, your capabilities, or a LICENSE — those are the
decisions that make it an agent instead of a copy.

## Part 3 — Build it

```bash
uv run agents verify
```

Run this whenever you want to know where you stand. It runs every check CI
runs and prints all of them at once. **It will fail at first, and the list it
prints is your to-do list.** When it prints `All 7 gates pass`, you are done.

If you get stuck, paste the whole `verify` output into Claude along with this
page — the errors name the file and the fix.

## Part 4 — Open the pull request

```bash
git add -A && git commit -m "Add my-agent"
git push -u origin my-agent
```

Then open the pull request on GitHub. CI runs the same gates. Fill in the
checklist in the PR template.

---

## Part 5 — The contract (this is the part to paste into Claude)

Everything below defines what a correct agent looks like here. Paste from
here to the end of the page.

### What an agent is

An agent is **called, not booted**. It is a program that reads one JSON
request on stdin, writes exactly one JSON envelope to stdout, and exits. It
is not a server. It has no ports, no health checks, no startup.

The orchestrator runs it as a subprocess with the agent's own folder as the
working directory, and passes it only the environment variables the agent
declared.

### The wire format — `agentcall/v1`

Request in, on stdin:

```json
{"protocol": "agentcall/v1", "capability": "normalize_address",
 "input": {"address": "123 north main street"}}
```

Success envelope out, on stdout:

```json
{"protocol": "agentcall/v1", "ok": true, "capability": "normalize_address",
 "output": {"normalized": "123 N MAIN ST"},
 "usage": {"input_tokens": 0, "output_tokens": 0, "cost_micros": 0},
 "error": null}
```

Failure envelope out, on stdout:

```json
{"protocol": "agentcall/v1", "ok": false, "capability": "normalize_address",
 "output": null,
 "usage": {"input_tokens": 0, "output_tokens": 0, "cost_micros": 0},
 "error": {"type": "invalid_request", "message": "'address' must be a non-empty string",
           "retryable": false}}
```

There are exactly five error types. Do not invent a sixth.

| Type | Means |
|---|---|
| `invalid_request` | The caller sent something wrong. |
| `unavailable` | A dependency is absent or down — a missing credential, an unreachable database. |
| `timeout` | It took too long. |
| `internal` | It broke in a way it did not anticipate. |
| `transport` | **Orchestrator-side only. Your agent never emits this.** |

### The six rules that get broken

These are not style preferences. Each one is a real defect that the review
board blocks on.

**1. stdout carries the envelope and nothing else.** One stray `print()`, or
one log line from a library, makes the response unparseable. Point
`sys.stdout` at stderr before doing any work, and write the envelope to the
real stdout you saved first. The template already does this — do not undo it.

**2. Exit 0 whenever you produced an envelope**, including for failures. A
business failure is a *successful call that returned a failure*. Exiting
non-zero means "I could not produce an envelope at all".

**3. A missing credential returns `unavailable`, never a crash.** Write
`os.environ.get("MY_KEY")` and check it. Never `os.environ["MY_KEY"]` — that
raises `KeyError`, which becomes an `internal` error, which tells the caller
your agent is broken when actually it is unconfigured.

**4. Validate your own input.** The schemas in `agent.yaml` are
documentation; nothing enforces them for you. Check types and ranges
yourself and return `invalid_request` with a message naming what was wrong.

**5. Declare only the environment you actually use.** `runtime.env.inherit`
lists variable names. Listing one your code never reads is a finding — an
agent that grades photos has no business being handed a database password.
An empty list is a good answer if you need nothing.

**6. `describe` must be cheap.** It has to answer on a machine that cannot
run the rest of your agent, so keep heavy imports inside the capability that
needs them, not at the top of the file.

### `agent.yaml`

```yaml
protocol: agentcall/v1
name: my-agent            # must equal the folder name
description: >-
  One line. This is what a human reads in `agents list`, and what another
  agent would route on. Say what it does and for whom.

runtime:
  type: subprocess
  command: ["python3", "agent_main.py"]
  test: ["python3", "-m", "unittest", "discover", "-s", "tests"]
  env:
    inherit: []           # name only what a capability actually reads

capabilities:
  - name: describe        # required, always
    description: Report this agent's name and capabilities. Costs nothing.
    input_schema:
      type: object
      properties: {}
      additionalProperties: false
    output_schema:
      type: object
      required: [name, protocol, capabilities]
      properties:
        name: { type: string }
        protocol: { type: string }
        capabilities: { type: array, items: { type: object } }

  - name: your_capability
    description: >-
      What it does, what it costs, and how it fails. Someone choosing between
      agents reads this.
    input_schema:
      type: object
      required: [thing]
      properties:
        thing: { type: string, description: What it is }
      additionalProperties: false
    output_schema:
      type: object
      required: [result]
      properties:
        result: { type: string }
```

Every capability in the code must be declared here, and vice versa. A
mismatch fails `agents check`.

### Shape of the code

Keep the protocol adapter and the actual work in separate files.

- `agent_main.py` — parses the request, validates it, calls your logic,
  builds the envelope. **No business logic here.**
- `<something>.py` — your actual work. Imports nothing about the protocol.

This is not decoration: it is what lets your logic be tested directly, and it
is one of the things the board checks.

### What your folder must contain

| | |
|---|---|
| `agent.yaml` | The manifest above |
| `agent_main.py` | The adapter |
| `README.md` | What it does, how to run it, what it will **not** do |
| `LICENSE` | Pick one deliberately — nothing is inherited |
| `tests/` | Tests that run the real entrypoint |

### What good looks like

Tests should call your agent the way the orchestrator does — as a subprocess
— so that stdout hygiene and the exit code are covered rather than assumed:

```python
proc = subprocess.run([sys.executable, "agent_main.py"],
                      input=json.dumps(request), capture_output=True, text=True)
assert proc.returncode == 0
envelope = json.loads(proc.stdout)      # fails if anything else was printed
```

Cover at least: `describe` matching the manifest, an unknown capability, a
malformed input, and each error type your agent can return.

### Things that will get your PR sent back

Real findings from a real review of a deliberately bad agent:

- The description was still the template's placeholder text.
- One capability called `run` that "figures out what you meant" — a
  grab-bag. One capability does one thing, and its name says which.
- `inherit` listed `AWS_*`, `GITHUB_TOKEN` and `JWT_SECRET`, none of which
  the code read.
- SQL built by string concatenation from user input.
- `os.environ["MLS_API_KEY"]` crashing instead of returning `unavailable`.
- Tests that would still pass if the capability were deleted.
- A capability that duplicated what an existing agent already did — it
  should have been a capability *of that agent* instead.

That last one is worth thinking about **before you write code**: does your
agent stand on its own, or is it a feature of something already here?

---

## Ask Claude this

After pasting the above, describe your agent:

> Using the contract above, build me an agent called `<name>` that `<what it
> does>`. It should offer these capabilities: `<list them>`. Give me
> `agent.yaml`, `agent_main.py`, the logic module, the README, and the tests
> as complete files. Follow all six rules. Do not put business logic in
> `agent_main.py`.

Then save each file into your folder and run `uv run agents verify`.
