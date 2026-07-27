# Roadmap

Where this repository is, where it is going, and what stands between.

**The target.** A multi-tenant agent platform: contributors anywhere add
agents by pull request, the repository guides them while they build, a review
board decides whether the agent is good enough, and the platform runs what is
merged — with each tenant isolated from the others.

**Today.** A monorepo with a deterministic contract, a local orchestrator, one
production agent, and a review board that has never run. The distance between
those two sentences is this document.

Status: ✅ done · 🟡 partial · ⬜ not started · 🔒 blocked on a human

---

## W1 — The contribution funnel

How an idea becomes a merged agent. This is the flow the repository exists to
serve, and one stage of it is empty.

| | Item | Status |
|---|---|---|
| 1.1 | Interview-driven scaffolding — *why does this agent exist, what is its distinct purpose, what capabilities* — writing the answers into `agent.yaml` | ⬜ |
| 1.2 | `_template` a copyable working agent with starter tests | ✅ |
| 1.3 | Integration completeness — a renamed template is rejected | ✅ |
| 1.4 | `/raise-pr` — gates, then board, then PR or denial | 🟡 written, never executed |
| 1.5 | Review board — four reviewers, one lens each | 🟡 written, never executed |
| 1.6 | CI review gate on the pull request | 🟡 needs secret + branch protection |
| 1.7 | First end-to-end run against a deliberately bad agent | ⬜ |

**1.1 is the only stage with nothing behind it.** Without it a contributor
scaffolds by guesswork, meets four reviewers, and iterates blind.

## W2 — Guidance while they build

The repository should teach at the moment of the mistake, not in a document
nobody reopens. Three mechanisms, each doing what only it can.

| | Item | Mechanism | Status |
|---|---|---|---|
| 2.1 | `/new-agent` — the interview | skill | ⬜ |
| 2.2 | Contract rules that load when editing a manifest or entrypoint | `.claude/rules/` with `paths:` | ⬜ |
| 2.3 | Manifest edited → integration check runs and reports | `PostToolUse` hook | ⬜ |
| 2.4 | Committed team settings so guidance applies to every clone | `.claude/settings.json` | ⬜ |
| 2.5 | The `agents` CLI as the contributor's tool — `list`, `describe`, `call`, `check` | tool | ✅ |
| 2.6 | Reference material loaded on demand rather than always | skill supporting files | ⬜ |

**Rules guide, hooks enforce.** A rule is context; a hook exits non-zero. Use
a rule for "prefer this shape", a hook for "this is not valid".

## W3 — Multi-tenancy

What changes when contributors are strangers rather than colleagues. Nothing
here exists yet, and most of it is cheap.

| | Item | Why | Status |
|---|---|---|---|
| 3.1 | `CODEOWNERS` mapping each agent folder to its author | Review routing, and one tenant cannot silently edit another's agent | ⬜ |
| 3.2 | `owner` in `agent.yaml` | The manifest should say whose it is | ⬜ |
| 3.3 | `version` in `agent.yaml`, and a compatibility policy | Agents change; callers need something to pin | ⬜ |
| 3.4 | Lifecycle the orchestrator honours — `active`, `deprecated`, `disabled` | `status` exists and nothing reads it | 🟡 |
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

Detail in [`CERTIFICATION_ROADMAP.md`](./CERTIFICATION_ROADMAP.md). Summary:

| Domain | Weight | Status |
|---|---|---|
| Agentic Architecture & Orchestration | 27% | ⬜ no agentic loop anywhere |
| Claude Code Configuration & Workflows | 20% | 🟡 CLAUDE.md, commands, reviewers; W2 closes most of the rest |
| Prompt Engineering & Structured Output | 20% | ✅ `photo_grader` is the reference |
| Tool Design & MCP Integration | 18% | 🟡 tool design yes, MCP nothing |
| Context Management & Reliability | 15% | ✅ provenance, confidence, human review |

## W7 — Operations

| | Item | Status |
|---|---|---|
| 7.1 | Deterministic CI, scoped to what changed | ✅ |
| 7.2 | `ANTHROPIC_API_KEY` repository secret | 🔒 |
| 7.3 | **review board** required in branch protection | 🔒 |
| 7.4 | Repository visibility decided — it is Public and `realty-lead-gen/LICENSE` says proprietary | 🔒 |
| 7.5 | Secret scanning at root | ✅ |
| 7.6 | Repository off iCloud-synced storage | 🔒 |

---

## Order of work

**Now — W1.1 and W2.** The funnel has a strict back gate and no front. Every
hour spent here is repaid by every contribution afterwards, and it closes most
of the 20% certification domain as a side effect.

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
