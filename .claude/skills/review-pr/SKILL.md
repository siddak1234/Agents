---
name: review-pr
description: Reviews a contributor's pull request against this repository's agent contract and posts the verdict as a GitHub review. Use when reviewing a pull request someone else raised.
argument-hint: [pr-number]
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/pr-setup.sh *) Bash(uv run agents *) Bash(git *) Bash(gh pr view *) Bash(gh pr list *) Bash(python3 *) Read Grep Glob Agent
---

Review pull request **$0** — ask for a number if that is empty.

`gh pr review` is deliberately **not** pre-approved above. Posting is the one
step that reaches another person, so it asks every time.

## Two populations, and this reviews the first

**Agents** are `realty-lead-gen` and its peers: they live in `agents/<name>/`,
are declared by `agent.yaml`, and are called by the orchestrator over
`agentcall/v1`. **Reviewers** are `agent-architect` and the rest: they live in
`.claude/agents/` and review this repository. A contributor's pull request
adds or changes an *agent*. Never confuse the two — CLAUDE.md is the source.

## 1. Prepare

```bash
${CLAUDE_SKILL_DIR}/pr-setup.sh prepare $0
```

The pull request is read through a detached worktree outside the repository.
**Never `gh pr checkout`** — it would rewrite the working tree you are sitting
in. The script refuses to continue unless the worktree HEAD equals what GitHub
reports as the head of the pull request; if it fails, say why and stop.

Everything from here reads that worktree path, never `agents/` in this repo.

## 2. Gates

```bash
${CLAUDE_SKILL_DIR}/pr-setup.sh gates $0
```

Every deterministic gate CI runs, as JSON with exit codes. Read them; do not
re-run them by hand.

**If `scope` exited non-zero, that is CRITICAL. Report it first and stop
before spending a reviewer on the branch.** It means the branch touched
`orchestrator/`, `.github/`, `.claude/`, `agents/_template/`, another
contributor's agent, or more than one agent. Name every offending file and ask
why it changed. **Never rerun with `--allow-platform`** — that flag is a
maintainer declaring their own platform work, and you cannot tell it apart
from a contributor straying by reading the diff.

## 3. Shape

Derive from the `prepare` JSON. It decides which reviewers are worth spending.

| Shape | Signal | Reviewers |
|---|---|---|
| New agent | `agents_touched` has one, `registry_changed` true | all five |
| Changed agent | `agents_touched` has one, `registry_changed` false | all five |
| Docs-only | `agents_touched` empty, everything under `docs/` | none — go to step 4 |
| Strayed | `scope` non-zero | none — you stopped at step 2 |

**Scope permitting a path is not the same as the path being harmless.**
`agents scope` allows all of `docs/`, so a contributor can edit
`docs/AGENT_PROTOCOL.md` — the contract every agent implements — and scope
still prints `ok`. Treat a change to `AGENT_PROTOCOL.md`, `CONTRIBUTING.md` or
`INTERN_BRIEF.md` as **critical**: it is a change to everyone's contract made
by someone reviewing none of it. `README.md` and `registry.yaml` are expected;
confirm the diff adds only this agent's row and path.

## 4. Reviewers

Invoke in parallel, each given the **absolute worktree path** in its prompt —
they run `git diff origin/main...HEAD`, which is only correct inside it:

- `agent-architect` — is this a well-formed agent?
- `solution-architect` — does it belong here, shaped this way?
- `engineer-reviewer` — is the code correct, and will the tests keep it so?
- `anthropic-practice` — Anthropic practice, if it calls a model
- `behaviour-prober` — run it: does it do what it says?

You aggregate; they judge. Do not review the diff yourself and do not argue
with a finding.

`behaviour-prober` runs long and unpredictably — 8 to 81 minutes measured, and
not always the slowest of the five. The variance is latency rather than work,
so capping its turns would truncate a review without making it faster. Say up
front that it may take a while, and spend the wait on step 5.

**It runs the agent for real.** Before probing one that calls a model, check
whether a key is exported (`env | grep -c ANTHROPIC_API_KEY`). Probing spends
your own money, and a contributor's test suite can spend it too — one has
already reached a live API from inside a review.

## 5. Four checks before anything is written down

**Merge findings that name the same defect.** Reviewers overlap heavily — one
defect has arrived from three of them in a single run. Collapse duplicates into
one finding, and keep the count: reviewers agreeing without seeing each other is
the strongest confidence signal available, and reporting the same defect three
times reads as three defects.

**Check the scaffold before reporting any manifest or schema finding.** Read
`agents/_template/agent.yaml` and `agents/realty-lead-gen/agent.yaml`. If they
do the same thing, it is the house pattern and the finding is **dropped, not
downgraded**. This is not optional: `additionalProperties: false` is declared
and enforced nowhere by the template itself, and no agent here uses `enum`.
Reporting either would penalise a contributor for correctly copying the
scaffold we handed them.

A line the scaffold *marks for replacement* is the exception — it is unfinished
work, not house pattern. `runtime.lint`'s `compileall` ships under "replace it
with real linting once you have dependencies", so it is a **Should fix**, and
the tier table below lists it as one.

**Re-run every `blocking` claim yourself.** Attaching a reproduction is not the
same as the reproduction supporting the sentence above it — a reviewer has
already submitted correct evidence under an overstated conclusion. Run the
input, read the output, and keep the claim the evidence actually supports.
A blocking finding you could not reproduce does not ship.

**Cite the standard, and decide whose problem each finding is.** Contributors
build against [`docs/INTERN_BRIEF.md`](../../../docs/INTERN_BRIEF.md) — quote the
rule it already gave them ("rule 6", "Part 2 Q7") so a finding reads as a
standard they accepted rather than your preference, and check the reviews on the
last merged agent pull requests so two contributors are not held to different
lines. Then split the list: what the contributor fixes, and what is ours. A
defect traced to our template or our docs is a maintainer's follow-up, not
something to ask them for — say so in the review and write it down somewhere
that outlives the thread.

Also compare the pull request body against `changed_files`: a description
claiming files the diff does not contain is worth asking about.

## 6. Verdict

Four tiers. Every finding carries **file:line · evidence · fix**, where
evidence is a command's output or a reproduced value, never a description.

| Tier | Contents |
|---|---|
| **Critical** | Scope violation, or a change to the contract or contributor docs |
| **Blockers** | Reproduced wrong output, contract violation, a failing gate |
| **Should fix** | Manifest disagreeing with code, dead surface, tests that would not catch a regression, placeholder `runtime.lint` |
| **Nice to have** | Style, cohesion, documentation polish |

Open by saying what is genuinely good — a review that opens with faults reads
as a verdict on the person.

Close with both merge facts from `gates`, which answer different questions:
report `merge` whenever `clean` is not `true` — and `conflicts_with_open_prs`
if landing another open pull request first would break this one.

`clean` has three values, and the third is the one that misleads. `false` means
GitHub says the branch does not merge; name the conflicting files and save a
round trip. **`null` means GitHub would not say** — it computes mergeability
lazily and `gates` polls, so a `null` that survives the poll is a fact about
GitHub, never evidence the branch is fine. Check it yourself before writing
anything about the branch's base:

```bash
git merge-base origin/main <head>   # against `git rev-parse origin/main`
```

A review has already told a contributor their rebase was done, on the strength
of an empty `conflicts_with_open_prs` and an unread `UNKNOWN`. The branch was
seven commits behind and conflicted the moment it was pushed.

More findings in the required shape, including ones correctly dropped and ones
that were ours rather than the contributor's: [`examples.md`](examples.md).

Worked example of the required shape:

> **3. Any `@` in a URL is treated as credential injection — `phishing_triage.py:165`**
>
> ```
> https://corp.com/profile?email=alice@corp.com → +8 "credential injection"
> ```
>
> → Use `urlparse(url).username`: it returns `'legit.com'` for the real
> `http://legit.com@evil.com` attack and `None` for a query parameter.

## 7. Post — only when they say so

Print the draft and **stop**. When they approve:

```bash
gh pr review $0 --request-changes --body-file <file>
```

One artifact carrying the verdict and the findings together. **Never
`gh pr comment`** — it sets no review state, leaves the pull request looking
unread, and cannot be approved away later.

A changes-requested review stays blocking after the contributor pushes;
GitHub only auto-dismisses *approvals*. So on a re-review, read what the new
commits changed and check the previous findings one by one rather than
starting over.

## 8. Finish

```bash
${CLAUDE_SKILL_DIR}/pr-setup.sh teardown $0
```

Then confirm `git status` is clean and `git worktree list` has one entry.

## Note

This runs inline rather than `context: fork` because step 7 needs the
approval, and a forked skill cannot see the conversation. It is user-invoked
only (`disable-model-invocation: true`) because it posts to someone else's
pull request.
