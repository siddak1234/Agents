# realty-lead-gen

Photo-condition grading agent: one Claude vision call per batch of listing
photos, returning a UAD condition grade and a rehab range over
`agentcall/v1`. Stack: Python 3.13, pydantic + pydantic-settings, httpx,
tenacity, structlog, the Anthropic SDK. Dependencies managed with `uv`.

The lead-generation *service* this grew out of — FastAPI, Postgres,
migrations, source adapters, jobs — lives in the `realty-lead-gen-service`
repository. Do not add service machinery back here: the repository's shape
rule (docs/CONTRIBUTING.md, "How big is an agent?") blocks it, and this folder is
the worked example of that rule.

## Commands

Run these from this folder, not the repository root.

```bash
make setup   # uv sync (never `pre-commit install` — see the Makefile)
make test    # unit tests
make lint    # ruff + mypy strict
make fmt     # ruff format
make hooks   # this folder's pre-commit hooks, run on demand
```

## House rules

- **Money is integer cents.** Never a float, never a bare Decimal on the
  wire.
- **Timestamps are timezone-aware UTC**, always — including in tests.
- **A missing key is a disabled capability, never a crash.** `grade_photos`
  returns `unavailable` without `ANTHROPIC_API_KEY`; `describe` answers
  regardless.
- **`mypy` runs strict and Ruff has the bandit (`S`) rules on.** Both run in
  `make lint`, `agents lint`, and CI; do not weaken a rule to make a commit
  pass.

## The agentcall entrypoint

`src/realty_lead_gen/agentcall.py` implements `agentcall/v1` (see
`docs/AGENT_PROTOCOL.md` at the repository root) and is what
`uv run agents call realty-lead-gen ...` invokes. It is an **adapter**: it
translates the wire protocol into calls on `realty_lead_gen.agents` and
back. No business logic belongs there.

Its capability list mirrors `agent.yaml` by hand. When you add a capability,
change both, then run `uv run agents check` from the repository root.

The two traps it exists to avoid, both easy to reintroduce:

- `configure_logging` sends structlog to **stdout** (`logging.py`). The
  adapter repoints `sys.stdout` at stderr before anything runs, because one
  log line on stdout makes the response envelope unparseable.
- `describe` must answer without importing `anthropic` or settings, so keep
  those imports inside the capability that needs them.

## CI

No workflow of its own. The root `orchestrator.yml` handshake job runs this
agent's declared `runtime.lint` and `runtime.test` commands whenever this
folder changes — the full service pipeline went to the service repository
with the service.
