---
name: new-agent
description: Scaffold a new agent in this repository by first establishing why it should exist, what its distinct purpose is, and what capabilities it offers. Use when someone wants to create, add, start or build a new agent here.
argument-hint: [what the agent should do]
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
---

You are helping someone add an agent to this repository. Their idea is:
`$ARGUMENTS` — ask if that is empty.

**Establish the agent before you scaffold it.** A folder is thirty seconds of
work; a purpose nobody can state is a pull request the review board will
refuse. Most of what `agent-architect` blocks on is decided in this
conversation, not in the code.

## 1. Understand what exists

Read `registry.yaml` and each registered agent's `agent.yaml`. You are looking
for one thing: **does this idea already have a home?** Two agents that would
always be called together are usually one agent with two capabilities.

If it overlaps, say so plainly and propose the capability instead. Adding a
capability to a good agent beats adding a mediocre agent.

## 2. Interview

Ask these one at a time, conversationally. Do not present them as a form, and
do not move on while an answer is still vague — a vague answer here becomes a
blocking finding later.

1. **Why should this exist?** What is true after it runs that was not true
   before? If the answer is "it calls the X API", keep going — that is how,
   not why.
2. **Who calls it, and what do they do with the result?** This decides the
   output shape more than anything else.
3. **What does it do — as capabilities?** Name each for what it achieves, not
   how it is implemented. Push back on a single capability that does four
   things, and on four that are always called together.
4. **For each capability: what goes in, what comes out?** Concrete fields,
   units, formats. "A location" is not an answer; latitude and longitude in
   decimal degrees is.
5. **What can go wrong?** Which failures are the caller's fault
   (`invalid_request`), which are a missing dependency (`unavailable`), which
   are transient (`timeout`). This is the error taxonomy, decided up front.
6. **What credentials or configuration does it need?** Name the exact
   environment variables. Anything you cannot justify per capability does not
   go in `runtime.env.inherit`.
7. **Does it call a model?** If so, the `anthropic-practice` reviewer applies
   — structured output through a tool schema, graceful degradation without a
   key, usage reported. Say so now.
8. **What machinery does it need?** If the honest answer includes a web
   server, a database it owns, Docker, or a worker queue, stop the interview
   there: that is a service, not an agent, and the board blocks service
   shape (CONTRIBUTING.md, "How big is an agent?"). Help them find the
   agent-sized core — the capability a caller actually invokes — and say
   plainly that the rest belongs in its own repository.

Then **write the answers back as prose** — two or three sentences on purpose
and a list of capabilities — and get agreement before touching the filesystem.
This paragraph becomes the description, and the description is how a human, and
later a router, picks this agent.

## 3. Scaffold

Only now:

```bash
uv run agents new <agent-name>
```

That does the mechanical half — copies the template, sets the name in the two
files that must agree, registers it in `registry.yaml`, adds the README row —
and deliberately leaves the thinking half alone. (This skill used to say
`cp -r _template` and list the registration steps by hand, duplicating the
shipped command it was supposed to teach.)

Then work through every `TODO(new agent)` marker using the interview answers:

- `agent.yaml` — the description you agreed, capabilities with real input and
  output schemas, `runtime.env.inherit` naming only what is justified
- `agent_main.py` — the capabilities replacing `greet`, validation for each
  input, the error types chosen in question 5
- `tests/test_agent.py` — keep `TestContract`, replace `TestGreet` with tests
  for the real capability including its failure paths
- `README.md` — what it does, how to run it, how to configure it
- `LICENSE` — the template ships none on purpose. Ask which licence; do not
  choose one for them

## 4. Prove it

```bash
uv run agents list --strict
uv run agents check <agent-name>
uv run agents call <agent-name> <capability> --input '{...}'
cd <agent-name> && python3 -m unittest discover -s tests
```

`--strict` is the honest one — it fails while the agent is still a renamed
template. Work through what it reports until it is silent. Do not declare
success on a green `agents check` alone; that only proves the agent runs.

## 5. Hand over

Tell them what to do next, in this order: implement the capability bodies,
extend the tests, then `/raise-pr`. Name the reviewers they will meet and what
each will ask — `CONTRIBUTING.md` has the table.

Do not run `/raise-pr` yourself. The contributor should have built and tested
the thing before it is reviewed.

## Refuse to scaffold when

- The purpose is still "it calls the X API".
- The capability list is one capability doing everything.
- It duplicates an existing agent and the author has not addressed why.
- The plan is service-shaped — a server, an owned database, Docker, a queue —
  and question 8 did not find an agent-sized core.

Say which, and go back to the question that was not answered. Scaffolding
around an unclear purpose produces a folder that has to be deleted later, and
the author learns nothing.
