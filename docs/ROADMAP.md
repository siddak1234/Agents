# Roadmap

Where this repository is, where it is going, and what stands between.

**The target.** A multi-tenant agent platform: contributors anywhere add
agents by pull request, the repository guides them while they build, a review
board decides whether the agent is good enough, and the platform runs what is
merged — with each tenant isolated from the others.

**Today.** A monorepo with a deterministic contract, a local orchestrator, one
production agent, and a review board that has now run twice — blocking a
deliberately bad agent and passing a correct one, both locally. `main` is
protected — pull request, one approval, code-owner review, deterministic
checks — and the arrival question is decided: **collaborators, not forks**.
What is left is the token. Until `CLAUDE_CODE_OAUTH_TOKEN` exists the board
skips itself and its job still exits 0, so it is deliberately not a required
check; requiring it now would be a green tick over a review that never ran.
That ordering matters because GitHub withholds secrets from fork pull requests, so
under the safe `pull_request` trigger this repository uses, the board can
never run on a fork's PR. (`pull_request_target` would hand secrets to
unreviewed fork code; `review.yml`'s header explains why that trade is
refused.) Gating therefore requires contributors pushing branches to this
repository (collaborators), or a human reading every fork diff. The distance
between those two sentences is this document.

Status: ✅ done · 🟡 partial · ⬜ not started · 🔒 blocked on a human

---

## W1 — The contribution funnel

How an idea becomes a merged agent. This is the flow the repository exists to
serve, and one stage of it is empty.

| | Item | Status |
|---|---|---|
| 1.1 | Interview-driven scaffolding — *why does this agent exist, what is its distinct purpose, what capabilities* — writing the answers into `agent.yaml` | ✅ `/new-agent` interviews before it scaffolds, and refuses |
| 1.2 | `_template` a copyable working agent with starter tests | ✅ |
| 1.3 | Integration completeness — a renamed template is rejected | ✅ |
| 1.4 | `/raise-pr` — gates, then board, then PR or denial | ✅ a command under `.claude/commands/`, and executed. Command rather than skill because where the two names collide the skill wins, so shipping both would mean two copies of one procedure — and raising a pull request should be user-invoked, never something Claude decides to do |
| 1.5 | Review board — four reviewers, one lens each | ✅ executed twice; blocked the bad agent, passed the good one |
| 1.6 | CI review gate on the pull request | 🟡 branch protection is on; needs the secret, then the check added |
| 1.7 | First end-to-end run against a deliberately bad agent | ✅ 10 blocking findings across three reviewers |

**Every stage now has something behind it.** What remains is 1.6: the board
advises. Branch protection is on; the token is what is left.

## W2 — Guidance while they build

The repository should teach at the moment of the mistake, not in a document
nobody reopens. Each mechanism below does what only it can.

| | Item | Mechanism | Status |
|---|---|---|---|
| 2.1 | `/new-agent` — the interview | skill | ✅ |
| 2.2 | Contract rules that load when editing a manifest or entrypoint | `.claude/rules/` with `paths:` | ✅ two rules, both path-scoped |
| 2.3 | Manifest edited → integration check runs and reports | `PostToolUse` hook | ✅ exit 2 on a broken manifest, silent otherwise |
| 2.4 | Committed team settings so guidance applies to every clone | `.claude/settings.json` | ✅ |
| 2.5 | The `agents` CLI as the contributor's tool — `list`, `describe`, `call`, `check`, `test`, `lint`, `new`, `verify`, `scope` | tool | ✅ |
| 2.6 | Reference material loaded on demand rather than always | skill supporting files | ⬜ |

**Rules guide, hooks enforce.** A rule is context; a hook exits non-zero. Use
a rule for "prefer this shape", a hook for "this is not valid".

## W3 — Multi-tenancy

What changes when contributors are strangers rather than colleagues. Nothing
here exists yet, and most of it is cheap.

| | Item | Why | Status |
|---|---|---|---|
| 3.1 | `CODEOWNERS` mapping each agent folder to its author | Review routing, and one tenant cannot silently edit another's agent | 🟡 the file exists and owns the platform paths; per-agent author rows wait on real contributors, and it enforces nothing until 7.3 |
| 3.2 | `owner` in `agent.yaml` | The manifest should say whose it is | ⬜ |
| 3.3 | `version` in `agent.yaml`, and a compatibility policy | Agents change; callers need something to pin | ⬜ |
| 3.4 | Lifecycle the orchestrator honours — `active`, `deprecated`, `disabled` | No `status` field exists anywhere yet; the README table's Status column is prose nothing reads or validates | ⬜ |
| 3.5 | Trust tier — `community` vs `verified` | A stranger's agent should not run at first-party privilege | ⬜ |
| 3.6 | Per-agent resource ceilings — wall clock, memory, output | Timeout and output cap exist; memory does not | 🟡 |
| 3.7 | Secret scoping proven, not just declared | `env.inherit` is name-level; a shared host needs a provider | 🟡 |

## W4 — Runtime

Running one agent locally and hosting many for strangers are different
problems. This workstream is where the honest gap is largest.

| | Item | Status |
|---|---|---|
| 4.1 | Subprocess transport, cwd and env isolation, bounded output | ✅ |
| 4.2 | Container isolation per invocation — filesystem, network, memory | ⬜ |
| 4.3 | A host that accepts a call, routes to an agent, returns the envelope | ⬜ |
| 4.4 | Structured per-invocation telemetry — agent, request id, duration, cost, verdict | ⬜ |
| 4.5 | Concurrency and backpressure | ⬜ |
| 4.6 | A second transport, proving the envelope really is transport-agnostic | ⬜ |

4.2 is the line between "a repo that can call an agent" and "a platform that
can run a stranger's". Subprocess isolation is not a security boundary.

## W5 — Evaluation and cost

Named directly in Anthropic's description of the Architect role, and entirely
absent.

| | Item | Status |
|---|---|---|
| 5.1 | Golden cases per capability, run in CI | ⬜ |
| 5.2 | `usage` aggregated across a run | ⬜ |
| 5.3 | Budget ceiling that short-circuits further calls | ⬜ |
| 5.4 | Human-in-the-loop gate for capabilities that must not run unattended | ⬜ |
| 5.5 | Feedback captured as labelled data — `realty-lead-gen.lead_feedback` is the pattern | 🟡 exists in one agent, unused at platform level |

## W6 — Certification alignment

This repository doubles as a teaching artifact for the **Claude Certified
Architect** exam. This table is the only copy — a second one lived in
`docs/CERTIFICATION_ROADMAP.md` and the two drifted, so that file was folded
in here.

| Domain | Weight | Status |
|---|---|---|
| Agentic Architecture & Orchestration | 27% | ⬜ no agentic loop anywhere — see the mismatch below |
| Claude Code Configuration & Workflows | 20% | 🟡 hierarchical CLAUDE.md, skills with frontmatter, path-scoped rules, a PostToolUse hook, four reviewers, the board in CI; remaining: 2.6 |
| Prompt Engineering & Structured Output | 20% | ✅ `photo_grader` is the reference, its tool schema pinned by a golden-file test |
| Tool Design & MCP Integration | 18% | 🟡 tool design yes, MCP nothing — neither a server nor a consumer exists |
| Context Management & Reliability | 15% | ✅ provenance, confidence, human review |

**The mismatch to be honest about.** The exam's Domain 1 means agentic loops:
coordinator–subagent patterns, context passing, termination on `stop_reason`.
`agentcall/v1` is single-shot process invocation — the substrate *beneath*
agentic orchestration, not an example of it. Anyone studying from this repo
today learns to wire a process boundary cleanly and learns nothing about
running a loop. Say that out loud until the reference loop agent exists: an
agent built on the Claude Agent SDK that runs a real gather → act → verify
loop internally and remains an ordinary `agentcall/v1` leaf to the
orchestrator — internally agentic, externally a subprocess, which is the
composition boundary this repo already claims. After it: an MCP server +
consumer pair, then evaluation and cost (W5).

Exam-shaped exercises, mock questions and curriculum scaffolding are not
planned. This is a working repository whose practices happen to be the exam's
subject matter — if it stops being a real repo in order to teach, it teaches
nothing worth knowing.

## W7 — Operations

| | Item | Status |
|---|---|---|
| 7.1 | Deterministic CI, scoped to what changed | ✅ |
| 7.2 | `CLAUDE_CODE_OAUTH_TOKEN` repository secret (no API key needed — `claude setup-token`) | 🔒 |
| 7.3 | **review board** required in branch protection | 🟡 protection is on with the two deterministic checks; the board is excluded on purpose until 7.2 — it exits 0 without a token |
| 7.4 | Repository visibility decided — it is Public and `realty-lead-gen/LICENSE` says proprietary | 🔒 |
| 7.5 | Secret scanning at root | ✅ |
| 7.6 | Repository off iCloud-synced storage | 🟡 both `.venv`s relocated out via symlink; the working copy itself is still on Desktop |

---

## Order of work

**Now — W1.6.** W1.1 and W2 are done, and `main` is protected. The board runs
and its findings are specific enough to act on, but nothing forces anyone to
listen: that is the token, then adding the check. Both need the owner.

**Next — W3.** Ownership, version, and trust tier are small manifest and
`CODEOWNERS` changes that become expensive to retrofit once agents exist.

**Then — W5, then W6 Domain 1.** Evaluation before more agents, so quality is
measurable rather than argued. Then the agentic-loop reference agent, which
needs a second real agent to be worth designing against.

**W4 last, and only when it is real.** Container isolation and a hosting layer
are a platform build, not a repository change. Do not start it because the
roadmap says "multi-tenant" — start it when a real caller needs it.

## What this repository will not do

- Ship an agent that needs a server started before it can be called.
- Add cross-agent machinery designed against one agent.
- Treat a line count as a proxy for structure.
- Let a review board block on taste. It blocks on defects, or it gets ignored.
