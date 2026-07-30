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

### 1. Put them on a branch

Stop only for the two things that make review impossible:

- **No diff against `origin/main`** — there is nothing to review.
- **A dirty working tree** — the gates below read the working tree, so
  uncommitted work would be tested and then not shipped. Say which files, and
  stop.

Being on `main` is **not** a reason to stop. It used to be, and it left an
intern who had committed to `main` with a refusal and no way forward. Instead:

```
git checkout -b <slug>
```

Build `<slug>` from the title by a fixed transform, so the same title always
gives the same branch: lowercase it, drop anything that is not a letter, digit
or space, drop a leading `a`/`an`/`the`, keep the first four to five remaining
words, join with hyphens. *"Add a Weather Forecast Agent (v2)"* →
`add-weather-forecast-agent`. Their commits come with them.

If they are already on a branch, **use it**. Do not create a second one; that
orphans the work they have been doing.

After the push in step 5, tell them their local `main` still points at those
commits and that `git reset --hard origin/main` on `main` tidies it. Do not
run it for them — it discards work if they have misread their own state, and
nothing here is worth that risk.

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

`scope` refuses a branch that changes shared code, including one that contains
nothing else. That is the rule, not a bug to work around: shared code is a
maintainer's to change.

**Never decide for yourself that a branch is platform work.** `--allow-platform`
is the one instruction here you could rationalise your way into, and a branch of
nothing but shared-code edits is exactly what both a maintainer's platform work
and a contributor straying out of their folder look like from the diff. You
cannot tell them apart by reading it. So ask, the same way you ask for a missing
title:

> This branch changes only shared code, which `agents scope` refuses. Are you a
> maintainer raising this as deliberate platform work? If so I will rerun with
> `--allow-platform`.

Rerun with the flag **only** after they say yes, and say in your output that you
did and that they authorised it. If they say no, stop — the fix is to move the
work into `agents/<their-agent>/`, not to pass the flag.

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

**No blocking findings → step 5.** Nothing reaches GitHub unless it is clean:
a branch that failed step 2 or was blocked here never becomes a pull request.

### 5. Push, open, comment

```
git push -u origin HEAD
gh pr create --title "<title>" --body "<body>"
```

The push is not optional and not implied — `gh pr create` on a branch with no
remote fails outright. If the push is rejected, say so plainly and **stop**:
that is write access or branch protection, not a fault in the branch, and
there is nothing to open a pull request against.

If the branch already has an open pull request, `gh` refuses rather than
opening a second one. That is not a failure either — the push above has
already updated it. Say so and print its URL.

The **body** is what the change does plus the board's verdict: every reviewer
that passed. Fill in the repository's pull request template rather than
replacing it.

The **advisory findings go in a comment**, not the body:

```
gh pr comment <url> --body "<findings>"
```

Ordered high to low severity, grouped by reviewer, each with its file, the
problem and the suggestion. A comment is a review surface — it can be replied
to and resolved — and a body cannot. This is the list the maintainer reads
before opening the diff, so rank it honestly rather than padding it.

If `gh pr comment` fails, print the findings in full to the terminal and say
they did not post. The pull request is already open by then, so a swallowed
error loses the entire review — the one output of this command nothing else
reproduces.

Finish by printing the pull request URL, and say plainly that merging waits on
the maintainer's approval.

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
