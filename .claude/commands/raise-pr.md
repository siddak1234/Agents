---
description: Run the review board on this branch and open a pull request only if it passes
argument-hint: [pull request title]
allowed-tools: Bash, Read, Grep, Glob, Agent
model: opus
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
uv run agents list --strict
uv run ruff format --check orchestrator tests _template
uv run ruff check orchestrator tests _template
uv run mypy orchestrator
uv run pytest -q
uv run agents check
```

If any fail: print the output, say what to fix, and **stop without calling the
board.** There is no sense paying an architect to review a branch that does
not lint, and the failures are usually the same ones the board would restate.

If the change touches an agent that has its own test command, run that too —
`realty-lead-gen` is the example.

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

The body must contain: what the change does, then the board's verdict — every
reviewer that passed, and any advisory findings carried forward so a human
reviewer sees what the board chose not to block on. Fill in the repository's
pull request template rather than replacing it.

Finish by printing the pull request URL.

## Note

This command is the fast path, not the gate. The same four reviewers run
again in CI on the pull request, and that run is what branch protection
enforces. Running them here means finding out in a minute rather than after a
push — it does not mean CI will agree if the branch changes afterwards.
