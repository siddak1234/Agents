---
description: Run the review board on this branch and open a pull request only if it passes. Use when someone wants to raise, open or submit a pull request for an agent they have built.
argument-hint: [pull request title]
allowed-tools: Bash, Read, Grep, Glob, Agent
---

## Context

- Branch: !`git rev-parse --abbrev-ref HEAD`
- Changed files: !`git diff --name-only origin/main...HEAD`
- Shape of the change: !`git diff --stat origin/main...HEAD`

## Your task

Take this branch through review and open a pull request titled `$ARGUMENTS`
(ask for a title if that is empty). Work in order and stop at the first
failure — reporting five problems at once teaches less than reporting the one
that matters.

### 1. Refuse the obvious

Stop if the branch is `main`, if there is no diff against `origin/main`, or
if the working tree is dirty. Say which, and stop.

### 2. Deterministic gates — these are free, so they run first

```
uv run agents verify
uv run agents scope --base origin/main
```

`verify` is every deterministic gate CI runs against the working tree. This
file used to enumerate the commands itself and the list drifted — it was
missing `agents lint` entirely — which is why the contributor-facing
definition of the gates lives in `agents verify`. `scope` is the one gate
`verify` cannot run for you, because it compares against a base rather than
reading the tree; CI runs both. (The CI workflows necessarily spell out their
own steps; they run the same set, and a gate added to one belongs in both —
`orchestrator/tests/test_verify.py` fails if they diverge.)

If either fails: print the output, say what to fix, and **stop without calling
the board.** There is no sense paying an architect to review a branch that does
not lint, and the failures are usually the same ones the board would restate.

Among those gates, `agents lint` and `agents test` run each agent's own
declared commands from its own folder, so the agent you just built is checked
too — there is nothing extra to remember.

### 3. The review board

Invoke these four subagents **in parallel**, each on this branch's diff:

- `agent-architect` — is this a well-formed agent?
- `solution-architect` — does it belong here, shaped this way?
- `engineer-reviewer` — is the code correct, and will the tests keep it so?
- `anthropic-practice` — does it follow Anthropic's practice for Claude agents?

Each returns a JSON verdict. Do not review the change yourself, and do not
argue with a reviewer's finding — you aggregate, they judge.

### 4. Decide

Collect every finding. Then:

**Any `blocking` finding → the pull request is not raised.** Print them
grouped by reviewer, each with its file, the problem, and the fix. Print
`advisory` findings underneath as suggestions. Say plainly that the branch
needs revision, and stop. Do not offer to fix them in this run — the
contributor revises, then runs `/raise-pr` again.

**No blocking findings → open the pull request:**

```
gh pr create --title "<title>" --body "<body>"
```

If the branch already has an open pull request, `gh` refuses rather than
opening a second one. That is not a failure of this command: push instead,
which updates the existing pull request, and say so.

The body must contain: what the change does, then the board's verdict — every
reviewer that passed, and any advisory findings carried forward so a human
reviewer sees what the board chose not to block on. Fill in the repository's
pull request template rather than replacing it.

Finish by printing the pull request URL.

## Note

This lives in `.claude/commands/` rather than `.claude/skills/` on purpose.
Both forms create `/raise-pr` on a current Claude Code, and where the two
collide the skill wins — so shipping both would leave a second copy of this
procedure that nothing keeps in sync. The command form is the one that also
works on older clients, and it is user-invoked only, which is right for
something that opens a pull request: `.claude/skills/new-agent/SKILL.md` says
in as many words not to run this on a contributor's behalf.

This is the fast path, not the gate. The same four reviewers run again in CI
on the pull request; that run becomes the gate once the owner adds a
`CLAUDE_CODE_OAUTH_TOKEN` secret and requires the **review board** check in
branch protection — until then it is advisory, and on fork pull requests it
cannot run at all (GitHub withholds secrets from forks). Running the board
here means finding out in a minute rather than after a push — it does not
mean CI will agree if the branch changes afterwards.

It runs in your own Claude Code session on your own subscription: no API key,
nothing to configure.
