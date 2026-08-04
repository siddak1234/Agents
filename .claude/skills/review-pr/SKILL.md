---
name: review-pr
description: Review a contributor's pull request against this repository's agent contract and post the verdict as a GitHub review. Use when reviewing a pull request someone else raised.
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

## 5. Two checks before anything is written down

**Check the scaffold before reporting any manifest or schema finding.** Read
`agents/_template/agent.yaml` and `agents/realty-lead-gen/agent.yaml`. If they
do the same thing, it is the house pattern and the finding is **dropped, not
downgraded**. This is not optional: `additionalProperties: false` is declared
and enforced nowhere by the template itself, and no agent here uses `enum`.
Reporting either would penalise a contributor for correctly copying the
scaffold we handed them.

**Re-run every `blocking` claim yourself.** Attaching a reproduction is not the
same as the reproduction supporting the sentence above it — a reviewer has
already submitted correct evidence under an overstated conclusion. Run the
input, read the output, and keep the claim the evidence actually supports.
A blocking finding you could not reproduce does not ship.

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
as a verdict on the person. Close with the `conflicts` result from `gates` if
another open pull request would clash.

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
