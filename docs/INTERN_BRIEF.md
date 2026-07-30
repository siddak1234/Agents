# Building your agent

You are going to add one agent to this repository. This page is everything
you need. Parts 1 to 5 are for you to follow; **Part 6 is the part you paste
into a Claude conversation**, along with your own answers from Part 2. There
is a ready-made prompt at the end.

**You do not need Claude Code, Copilot, Cursor or any coding tool.** Everything
here works with git, `uv`, a text editor, and any free chat assistant you can
paste text into. That is the path this page is written for. If you *do* have
Claude Code, two shortcuts exist and are pointed out where they apply
(`/new-agent` in Part 3, `/raise-pr` in Part 5) — they save typing, nothing
more, and the result is judged identically.

This file is self-contained on purpose. You can read it on GitHub, or grab it
without cloning anything:

```bash
curl -O https://raw.githubusercontent.com/siddak1234/Agents/main/docs/INTERN_BRIEF.md
```

Two agents have been built from this page alone, by someone who had never
seen the repository and was not allowed to read any other file in it. Both
passed every gate on the first run. If something here is not enough, that is
a bug in this page — say so.

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
git remote add upstream https://github.com/siddak1234/Agents.git
git fetch upstream
uv sync --frozen
uv run agents list          # should print the agents that already exist
```

If that last command prints a list, you are ready.

**Do not skip the `upstream` remote.** After forking, your `origin` is *your*
copy of the repository, and `origin/main` freezes at the moment you forked.
Every check that compares your branch against "main" must compare against
`upstream/main` — the real one — or it silently judges your work against a
stale snapshot and passes things it should refuse. Refresh it with
`git fetch upstream` whenever you have been away.

## Part 2 — Decide what your agent is

**This is your job, and it is the hard part.** Nobody is going to hand you a
scope. Most of what the review board sends back is decided here, before a
line of code exists — the worked failure at the end of this page failed on
*design*, not on code that didn't work.

Answer these eight, in writing, before you scaffold anything. Do not move on
while an answer is still vague; a vague answer here becomes a blocking
finding later.

1. **Why should this exist?** What is true after it runs that was not true
   before? If your answer is "it calls the X API" — that is *how*, not *why*.
   Keep going.
2. **Who calls it, and what do they do with the result?** This decides your
   output shape more than anything else.
3. **What does it do, as capabilities?** Name each for what it *achieves*,
   not how it is implemented. Two traps, and the board checks for both: one
   capability that does four things, and four capabilities that are always
   called together.
4. **For each capability, what goes in and what comes out?** Concrete fields,
   units, formats. "A location" is not an answer. "Latitude and longitude in
   decimal degrees" is.
5. **What can go wrong?** Sort each failure now: the caller's fault
   (`invalid_request`), a missing dependency (`unavailable`), or transient
   (`timeout`). This is your error taxonomy and it is easier to decide up
   front than to retrofit.
6. **What credentials or configuration does it need?** Name the exact
   environment variables. Anything you cannot justify against a specific
   capability does not belong in `runtime.env.inherit`.
7. **Does it call a model?** If so you also need structured output through a
   tool schema, graceful degradation when the key is absent, and usage
   reported. Decide that now, not later.
8. **What machinery does your plan need?** The right answer is almost
   nothing: a finished agent is usually the template's four files plus a
   module or two of real work. If your plan includes a web server, a
   database you own, Docker, or a background worker, stop — that is a
   **service**, not an agent, and the review board blocks service shape.
   An agent is *called*: one request in, one envelope out, no "start this
   first" step. Find the capability a caller actually invokes and build
   that; persistent storage and serving are the caller's problem or a
   separate system's.

Then write two or three sentences of prose describing the purpose, plus the
capability list. **That paragraph becomes your `description`** — it is how a
human, and later a router, picks your agent out of a list.

### Stop and rethink if any of these is true

- The purpose is still "it calls the X API". Forwarding a request to one
  endpoint and returning its response is a wrapper, not an agent — there is
  no decision in it that the caller could not have made.
- One capability does everything. If its description needs the word "or"
  three times, it is several capabilities wearing a coat.
- Something already in this repo does this. Run `uv run agents list` and
  read the descriptions. If an existing agent owns this ground, the right
  contribution is usually a **capability added to that agent**, not a new
  one. If you still think it should stand alone, be ready to say why the two
  data models can safely diverge.
- You cannot say what a caller does with the output. Then you do not yet
  know what the output should be.
- Your file list is growing past a dozen and you have not written a
  capability yet. Size is a symptom, not the offence — but it is the symptom
  of building a system around the agent instead of the agent.

### "Isn't this just a function?"

Small and deterministic is fine — an agent does not have to call a model, and
some of the best ones here do not. The question is not size, it is whether
the thing is **worth calling by name from somewhere else**:

- Does it own knowledge or rules that the caller should not have to carry?
  A table of unit conversions, an address normalisation standard, a boundary
  file — that is knowledge, and centralising it means one place to correct.
- Does it have failure modes worth naming? If every input either works or is
  invalid, and nothing can be unavailable or slow, it may genuinely be a
  library function.
- Would two different callers want the same answer? If only your code will
  ever call it, it belongs in your code.

One yes is enough. A unit converter with a real conversion table qualifies; a
function that adds two numbers does not.

A good agent is one someone else could pick out of `agents list` and call
correctly without asking you a question.

**If you have not done Part 1 yet**, you cannot run `agents list` and cannot
do the duplicate check. Do Part 1 first — it is two minutes, and skipping it
is how two people build the same agent.

## Part 3 — Create your folder

```bash
uv run agents new my-agent    # use your agent's real name, lowercase-with-hyphens
git checkout -b my-agent
```

*Have Claude Code? `/new-agent` interviews you through Part 2's questions first
and then runs exactly this command.*

**Work on the branch, not on your fork's `main`.** Nothing stops you committing
to `main` — branch protection lives on the upstream repository and does not
reach your fork — and it will appear to work for this agent. It breaks on your
next one: `agents scope` refuses a branch touching two agents, so if agent one
lives on your `main`, every later pull request carries it. Keep `main` a mirror
of `upstream/main` and it stays a clean place to branch from.

That creates `agents/my-agent/` from the template, sets the name in the two
files that must agree, registers it, and adds it to the README table. Agents
live under `agents/` — tenant space; everything outside it is the platform.
The command deliberately does **not** write your description, your
capabilities, or a LICENSE — those are the decisions that make it an agent
instead of a copy.

## Part 4 — Build it

```bash
uv run agents verify
```

Run this whenever you want to know where you stand. It runs every
deterministic check CI runs and prints all of them at once. **It will fail at
first, and the list it prints is your to-do list.** When it prints
`All 10 gates pass`, you are done. If it instead reports that some gates *did
not run*, that is not a pass — a tool or `.git` is missing locally, and CI
will still run them.

One more check sits outside this command, and it prints a reminder of it.
`agents scope` compares your branch against main, which needs a base this
command has no way to guess. On a fork that base is **`upstream/main`**, never
`origin/main` — see Part 1:

```bash
git fetch upstream
uv run agents scope --base upstream/main
```

It answers one question: did you change only your own agent? Your branch may
touch `agents/<your-agent>/`, `registry.yaml`, `README.md` and `docs/` —
nothing else. It refuses a branch of shared-code edits even if that is all it
contains. `--allow-platform` exists for maintainers and is not for you; if
scope refuses your branch, move the work into your agent's folder.

The ten, so you know what is being asked of you:

| Gate | Asks |
|---|---|
| `large files` | Is anything committed over 512 KB? An agent is source, not data. |
| `secret scan` | Did a credential end up in a committed file? (gitleaks) |
| `ruff format` | Is repository-owned code formatted? |
| `ruff check` | Is it lint-clean? |
| `mypy` | Does the orchestrator still type-check? |
| `pytest` | Do the contract and template tests still pass? |
| `agents list --strict` | Registered *and* integrated — LICENSE, README row, no TODO markers, your own description and capabilities |
| `agents check` | Does your agent run, and do its `describe` capability names match `agent.yaml`? |
| `agents lint` | Does your own declared lint command pass? Root tooling does not check your code. |
| `agents test` | Does your own declared test command pass, from your folder? |

The last four are about your agent. The first six are about the repository,
and should already pass — if one of them breaks, you changed something
outside your folder (the secret scan and the large-file cap being the
exceptions worth respecting: both fail on what *you* committed, and fixing
them means removing the file, never editing the scanner).

If you get stuck, paste the whole `verify` output into Claude along with this
page — the errors name the file and the fix.

### What your pull request may contain

Two rules, both enforced by the build rather than by a reviewer's patience:

**One agent, nothing else.** Your branch may touch `agents/<your-agent>/`,
`registry.yaml`, `README.md` and `docs/`. Not `orchestrator/`, not another
person's agent, not `.github/`, not `.claude/`. If your editor reformats a
shared file on save, the build fails and names it — that is the check working.

This applies even if your branch has no agent in it. A branch of nothing but
shared-code edits is refused, not waved through. Shared code is depended on by
every agent, so changing it is a maintainer's pull request reviewed as a change
to all of them — if you need something from the platform, say so in yours.

**Only files an agent ships.** Inside your folder: `.py`, `.md`, `.toml`,
`.json`, `.lock`, `.txt`, `.example`, plus `agent.yaml`, `LICENSE`,
`Makefile`, `.gitignore`, `.python-version`, `.pre-commit-config.yaml`.
A `docker-compose.yml`, a `Dockerfile`, an `alembic/` folder or a `.env`
fails — those mean you are building a service, not an agent.

`agents/_template/` is the whole of what an agent needs. If you are unsure
whether a file belongs, compare against it: it is the blueprint, it runs,
and CI exercises it on every build.

## Part 5 — Open the pull request

Run these four in order. Do not push until the first two are clean — a red
pull request costs the maintainer a review cycle you could have spent yourself.

```bash
uv run agents verify                             # must print: All 10 gates pass
git fetch upstream
uv run agents scope --base upstream/main         # must print: ok    scope: my-agent
git add -A && git commit -m "Add my-agent"
git push -u origin my-agent
```

Then open the pull request on GitHub, from your fork's branch to
`siddak1234/Agents` `main`. Fill in the checklist in the pull request template
— tick only what you actually ran.

**Before you open it, check these yourself.** Nothing automated will do it for
you on a fork, so this list is the review your work gets first:

- [ ] `agent.yaml` description is two or three sentences of your own — not the
      template's, not a restatement of the name
- [ ] Every capability in `agent_main.py` is in `agent.yaml`, and the reverse
- [ ] Each capability has a real `input_schema` and `output_schema`
- [ ] `runtime.env.inherit` lists only variables your code actually reads
- [ ] `agent_main.py` has no business logic — it translates the protocol and
      calls your module
- [ ] A missing credential returns `unavailable`; you never wrote
      `os.environ["KEY"]`
- [ ] Your tests would fail if the capability were deleted
- [ ] `LICENSE` exists and you chose it deliberately
- [ ] No `TODO(new agent)` marker left anywhere in your folder

### What happens next

CI runs the same deterministic gates on your pull request — they need no
credentials, so they run on a fork exactly as they ran for you. The four-reviewer
board does **not** run on a fork: GitHub withholds secrets from fork pull
requests, so a human reads your diff instead. That is why the checklist above
is worth your time.

You cannot merge your own pull request and you cannot push to `main` — it is
protected, and every change needs the maintainer's approving review. Expect to
revise. That is the process working, not you failing it.

*Have Claude Code? `/raise-pr` runs the gates, then the four reviewers, then
opens the pull request for you — and refuses to open one if a reviewer finds a
real defect. It is a shortcut, not a different standard.*

---

## Part 6 — The contract (this is the part to paste into Claude)

Everything below defines what a correct agent looks like here. Paste from
this heading to the end of the page — the closing prompt is part of it.

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
 "input": {"address": "123 north main street"},
 "request_id": "", "deadline_ms": 120000}
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

| Type | Means | `retryable` |
|---|---|---|
| `invalid_request` | Unknown capability, malformed input, wrong protocol — anything the caller got wrong. | `false` |
| `unavailable` | A dependency is absent or down — a missing credential, an unreachable database. | `false` if it is configuration, `true` if transient |
| `timeout` | The agent gave up inside its deadline. | `true` |
| `internal` | It broke in a way it did not anticipate. | `false` |
| `transport` | **Orchestrator-side only. Your agent never emits this.** | — |

`retryable` is not a judgement call — read it off that table. It tells a
caller whether trying again could possibly help.

**All of these are `invalid_request`**, and the skeleton below handles them —
do not remove any of the checks: stdin that is not valid JSON, a request that
is not a JSON object, a `protocol` that is not `agentcall/v1`, a missing or
non-string `capability`, an `input` that is not an object, and an unknown
capability. Your own field validation is on top of these, not instead.

`capability` is always present and always a string. Send `""` whenever the
request could not be parsed far enough to trust it — unreadable JSON, not an
object, or a `protocol` that is not ours. In that last case the field may
well be there and readable, but a request in an unknown protocol is not one
whose fields you should be echoing back.

### The six rules that get broken

These are not style preferences. Each one is a real defect that the review
board blocks on.

(Maintainers: this list deliberately restates the contract so the page stays
self-contained for a chat with no repo access. The mapping to
`docs/AGENT_PROTOCOL.md`: rules 1–4 here are §"Rules an agent must follow" 1–4;
rule 5 here restates the deny-by-default environment rule, which the protocol
states in §"What the orchestrator guarantees" rather than in its numbered
list; rule 6 here is the protocol's rule 5, "Import lazily". The protocol's rule 6 — declare every capability in `agent.yaml` —
is covered by this page's manifest section instead. Editing any of those
means editing both files.)

**1. stdout carries the envelope and nothing else.** One stray `print()`, or
one log line from a library, makes the response unparseable. Point
`sys.stdout` at stderr before doing any work, and write the envelope to the
real stdout you saved first. The skeleton below does this — do not undo it.

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
install your dependencies — that is how the whole repository is checked
without building every agent. So a heavy import goes *inside the function for
the capability that needs it*, never at the top of the file:

```python
def _score(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    import numpy            # imported only when this capability is called
    ...
```

That is also why each capability gets its own small handler function rather
than living inline in `dispatch` — `dispatch` stays a router, the handler
does the translating, and your real logic stays in its own module.

### `agent.yaml`

```yaml
protocol: agentcall/v1
name: my-agent            # must equal the folder name
description: >-
  Two or three sentences — the paragraph you wrote in Part 2. This is what a
  human reads in `agents list`, and what another agent would route on. Say
  what it does, for whom, and what it will not do.

runtime:
  type: subprocess
  command: ["python3", "agent_main.py"]
  test: ["python3", "-m", "unittest", "discover", "-s", "tests"]
  lint: ["python3", "-m", "compileall", "-q", "."]   # replace with real linting
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
mismatch fails `agents check`, which compares the **capability names** your
`describe` reports against the names in `agent.yaml` — a set against a set.
Descriptions and schemas are not compared, so they cannot drift you into a
failure, but keeping them honest is what the review board reads.

`describe` hardcodes its capability list in Python rather than parsing
`agent.yaml`. That is two places holding the same list on purpose: parsing
your own manifest would make `describe` agree with it by construction and
prove nothing. `agents check` exists precisely to catch them disagreeing.

### The skeleton

This is the template's `agent_main.py`, lightly adapted for this page — the
name is a placeholder and the `RULE n` comments are numbered to match the
list above, so this copy and that list cannot disagree. Start from this — it
already satisfies rules 1, 2 and 4. Replace `greet` with your capability, and
move anything that is real work into a separate module. (If you cloned the
repo, `uv run agents new my-agent` gives you the same skeleton with the
renames already done.)

```python
#!/usr/bin/env python3
"""One line about your agent."""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

PROTOCOL = "agentcall/v1"
AGENT_NAME = "my-agent"          # must equal the folder name

# Mirrors agent.yaml by hand. `agents check` fails if the two disagree.
CAPABILITIES = (
    ("describe", "Report this agent's name and capabilities. Costs nothing."),
    ("greet", "Return a greeting. Replace this with something useful."),
)


def main() -> int:
    # RULE 1. Do this before anything else can print.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        envelope = dispatch(sys.stdin.read())
    except Exception as exc:  # noqa: BLE001 — an envelope is mandatory
        traceback.print_exc(file=sys.stderr)
        envelope = fail("", "internal", f"{type(exc).__name__}: {exc}")
    finally:
        sys.stdout = real_stdout

    json.dump(envelope, real_stdout)
    real_stdout.write("\n")
    real_stdout.flush()
    return 0                      # RULE 2: an envelope was produced.


def dispatch(raw: str) -> dict[str, Any]:
    try:
        request = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        return fail("", "invalid_request", f"stdin is not valid JSON: {exc}")
    if not isinstance(request, dict):
        return fail("", "invalid_request", "request must be a JSON object")
    if request.get("protocol") != PROTOCOL:
        return fail("", "invalid_request", f"unsupported protocol {request.get('protocol')!r}")

    capability = request.get("capability")
    if not isinstance(capability, str):
        return fail("", "invalid_request", "missing 'capability'")

    payload = request.get("input") or {}
    if not isinstance(payload, dict):
        return fail(capability, "invalid_request", "'input' must be an object")

    if capability == "describe":
        return ok(capability, {
            "name": AGENT_NAME,
            "protocol": PROTOCOL,
            "capabilities": [{"name": n, "description": d} for n, d in CAPABILITIES],
        })

    if capability == "greet":
        # RULE 4: validate your own input, and say what was wrong.
        who = payload.get("name", "world")
        if not isinstance(who, str) or not who.strip():
            return fail(capability, "invalid_request", "'name' must be a non-empty string")
        return ok(capability, {"greeting": f"Hello, {who.strip()}!"})

    declared = ", ".join(n for n, _ in CAPABILITIES)
    return fail(capability, "invalid_request", f"unknown capability; this agent offers: {declared}")


def ok(capability: str, output: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL, "ok": True, "capability": capability,
        "output": output, "usage": zero_usage(), "error": None,
    }


def fail(capability: str, etype: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL, "ok": False, "capability": capability,
        "output": None, "usage": zero_usage(),
        "error": {"type": etype, "message": message, "retryable": retryable},
    }


def zero_usage() -> dict[str, int]:
    # Always report usage, zeroed when nothing was spent. Optional accounting
    # is accounting that gets forgotten.
    return {"input_tokens": 0, "output_tokens": 0, "cost_micros": 0}


if __name__ == "__main__":
    raise SystemExit(main())
```

`python3 agent_main.py` puts the script's own folder on `sys.path`, so a
sibling `units.py` is imported as `import units`.

### Dependencies

The template is standard-library only, which is why `command:` is a bare
`["python3", "agent_main.py"]` and it runs anywhere with nothing installed.
**Prefer that.** If you genuinely need a third-party package, add a
`pyproject.toml` to your folder and point the manifest at your own
environment:

```yaml
runtime:
  command: ["uv", "run", "python", "agent_main.py"]
  test: ["uv", "run", "--frozen", "--extra", "dev", "pytest", "-q"]
```

Your dependencies stay inside your folder — they never touch the orchestrator
or another agent. This is also what rule 6 is about: `describe` has to answer
on a machine that cannot install your dependencies, so keep heavy imports
inside the capability that needs them.

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
| `agent_main.py` | The adapter — protocol in, protocol out, no business logic |
| `<your-logic>.py` | The actual work. Imports nothing about the protocol. |
| `README.md` | What it does, how to run it, what it will **not** do |
| `LICENSE` | Pick one deliberately — nothing is inherited. MIT is fine; put your own name on it. |
| `tests/` | Tests that run the real entrypoint. No `__init__.py` needed. |

Plus two edits outside your folder, which `agents new` writes for you. If you
built the folder by hand, make them yourself — without them your agent is
never callable, because discovery is by declaration and never by globbing.

`registry.yaml` at the repository root — it opens with a comment header
explaining discovery-by-declaration; leave that alone and add the last line:

```yaml
version: 2

agents:
  - path: agents/realty-lead-gen
  - path: agents/my-agent
```

Paths only. Everything else about your agent lives in your own `agent.yaml`.

The agents table in the root `README.md` — add the last row:

```markdown
## Agents

| Agent | Status | Capabilities |
|---|---|---|
| [`realty-lead-gen`](./agents/realty-lead-gen) | active | `grade_photos` |
| [`my-agent`](./agents/my-agent) | active | `your_capability` |
```

`agents new` writes that row for you with a literal `TODO` in the last cell.
Nothing checks it afterwards — the marker scan looks for `TODO(new agent)`,
not a bare `TODO` — so replacing it with your real capabilities is on you.

`active` is the status to use. List your real capabilities, not `describe` —
every agent has that one.

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

Paste Part 6 above, then your own Part 2 answers:

> Using the contract above, build the agent I have scoped below.
>
> **Purpose:** <your two or three sentences — why it exists, who calls it,
> what they do with the result>
> **Name:** `<lowercase-with-hyphens>`
> **Capabilities:** <each one, with its inputs and outputs — concrete fields,
> units and formats>
> **Failure modes:** <which are invalid_request, which unavailable, which
> timeout>
> **Environment:** <the exact variables, or none>
>
> Give me `agent.yaml`, `agent_main.py`, the logic module, the README and the
> tests as complete files. Follow all six rules. Keep business logic out of
> `agent_main.py`.

If you cannot fill in that template, go back to Part 2 — the gap is in the
design, and Claude writing code around a gap will produce something that
compiles and fails review.

Then save each file into your folder and run `uv run agents verify`.
