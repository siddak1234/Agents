# Certification roadmap

This repo is a teaching artifact for the **Claude Certified Architect**
exam. This file records how it maps to the exam blueprint, what is missing,
and the order to close it in. Update it when a phase lands.

Blueprint domains and weights are from Anthropic's published exam guide.

## Where we stand

| Domain | Weight | Coverage |
|---|---|---|
| Agentic Architecture & Orchestration | 27% | **none** — see the mismatch below |
| Claude Code Configuration & Workflows | 20% | partial — hierarchical `CLAUDE.md` only |
| Prompt Engineering & Structured Output | 20% | good — `realty-lead-gen/agents/photo_grader.py` |
| Tool Design & MCP Integration | 18% | tool design yes, MCP **none** |
| Context Management & Reliability | 15% | good — provenance, confidence, human review |

## The mismatch to be honest about

The exam's Domain 1 means agentic loops driven by `stop_reason`,
coordinator–subagent patterns, context passing between subagents, and session
state — Claude agents invoking Claude agents inside a reasoning loop.

`agentcall/v1` is single-shot process invocation. It is the **substrate
beneath** agentic orchestration, not an example of it. Anyone learning from
this repo today learns to wire a process boundary cleanly and learns nothing
about running a loop.

Say this out loud to anyone using the repo to study, until Phase 1 lands.

## Phase 1 — a reference agentic-loop agent

Closes the largest and most misleading gap. Everything else can wait behind
it.

A new agent, `research-loop` or similar, built on the **Claude Agent SDK**,
that runs a real gather-context → act → verify loop: dispatches subagents,
passes context between them, terminates on `stop_reason`, and returns a
synthesised result through `agentcall/v1` like any other agent.

- **Teaches:** the agentic loop, coordinator–subagent decomposition, context
  passing, termination conditions, why a loop is not a chain of calls.
- **Also proves:** an agent may be internally agentic while remaining a leaf
  to the orchestrator — the composition boundary this repo already claims.
- **Size:** one agent folder. No orchestrator changes; if it needs one, that
  is a finding worth writing down.

## Phase 2 — MCP tool design

An agent that exposes its capabilities as an **MCP server**, with tool
interfaces written the way the exam tests: precise descriptions, structured
error responses, tools scoped so an agent is not handed thirty of them.

`realty-lead-gen` already contains a strong non-MCP example — the photo
grader's tool schema, pinned by a golden-file test. Phase 2 is that lesson
moved to MCP, plus one agent *consuming* an MCP server so both sides exist.

- **Teaches:** tool interface design, structured tool errors, tool
  distribution across agents, MCP client and server roles.

## Phase 3 — evaluation and cost

Named directly in Anthropic's description of the Architect role: *designing
end to end, planning for evaluation, cost, and safety.*

- **Evaluation:** an eval harness with golden cases per capability, run in
  CI. `realty-lead-gen` already has the raw material — `lead_feedback`
  captures accept / edit / dismiss, which is a labelled dataset waiting to be
  used.
- **Cost:** every envelope already carries `usage` and nothing sums it. Add
  aggregation across a run and a budget ceiling that short-circuits further
  calls — mirroring the per-lead cost breaker `realty-lead-gen` has
  internally.
- **Safety:** least privilege is done (`runtime.env.inherit`). Still missing:
  a human-in-the-loop gate for capabilities that should not run unattended.

## Phase 4 — Claude Code workflow surface

The remainder of Domain 3, each small on its own:

- Custom slash commands and skills with frontmatter.
- Path-specific rules under `.claude/rules/` with glob scoping.
- Claude Code in CI — review or triage running against a PR.

Hierarchical `CLAUDE.md` is already in place and is the largest piece.

## Not planned

Exam-shaped exercises, mock questions, or curriculum scaffolding. This is a
working repository whose practices happen to be the exam's subject matter. If
it stops being a real repo in order to teach, it teaches nothing worth
knowing.

## Prerequisite — met

CI runs. `orchestrator.yml` gates every change on ruff, strict mypy, registry
validation, the contract and template suites, and `agents check`;
`realty-lead-gen.yml` gates that agent on its own pipeline. Every practice
this repo teaches now fails a build when broken, which is the difference
between a repo that documents standards and one that holds them.
