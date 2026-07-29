"""The promise `agents verify` makes to CI, held against the workflow itself.

`verify` tells a contributor it runs every deterministic gate CI runs. That
claim has broken twice, the same way both times: a step was added to the
workflow and not to `_TOOL_STEPS`, so a branch could print "all gates pass"
and still go red. First gitleaks — a window in which a committed credential
passed locally. Then `check-added-large-files`.

Twice is a pattern, and the fix for a pattern is not a third careful reading.
This test is the promise: every deterministic step in CI's gate job must be
something `verify` runs, or something it names as deliberately out of scope.
Adding a gate to CI and nowhere else now fails here.

Scope is the `contract` job, which is CI's flat list of deterministic gates.
The `handshake` job wraps `agents check`, `agents lint` and `agents test` —
the same three `verify` runs — in shell that works out which agents a change
touches, and that scheduling is not a gate.

Everything here reads files and module constants. Nothing calls `_cmd_verify`,
which would run `pytest -q` inside pytest.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from orchestrator import cli

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "orchestrator.yml"

#: Fragments identifying a step that prepares the job rather than checking it.
#: Matched against the step's shell, not its name, because a name is prose.
SETUP_MARKERS = ("uv sync",)


def _covered_commands() -> list[str]:
    """Every command `verify` runs or disclaims, spelled as CI would write it.

    `_TOOL_STEPS` holds module names for `python -m`, so `pre_commit` becomes
    the `pre-commit` a workflow invokes.
    """
    commands = [f"{argv[0].replace('_', '-')} {' '.join(argv[1:])}" for _, argv in cli._TOOL_STEPS]
    commands += [label for label, _ in cli._AGENT_STEPS]
    commands += [label for label, _ in cli._NOT_COVERED]
    return commands


def _gate_job_steps() -> list[tuple[str, str]]:
    """(name, shell) for each step of the `contract` job that runs a command."""
    workflow: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return [
        (step.get("name", "(unnamed step)"), step["run"])
        for step in workflow["jobs"]["contract"]["steps"]
        if "run" in step
    ]


def test_every_ci_gate_is_run_by_verify_or_named_as_out_of_scope() -> None:
    covered = _covered_commands()
    unaccounted = [
        name
        for name, shell in _gate_job_steps()
        if not any(marker in shell for marker in SETUP_MARKERS)
        and not any(command in shell for command in covered)
    ]
    assert not unaccounted, (
        f"CI gate(s) {unaccounted} run in .github/workflows/orchestrator.yml but "
        f"not in `agents verify`, so a contributor can see every gate pass and "
        f"still get a red build. Add the command to `_TOOL_STEPS`, or — if it "
        f"genuinely cannot run locally — to `_NOT_COVERED` with the reason."
    )


def test_disclaimed_gates_name_a_real_subcommand() -> None:
    """A note pointing at a command that no longer exists teaches the wrong thing.

    `_NOT_COVERED` is printed to the contributor as the thing still standing
    between them and a green build, so its labels have to stay runnable.
    """
    choices = re.search(r"\{([^}]+)\}", cli._parser().format_usage())
    assert choices, "the parser stopped advertising its subcommands"
    subcommands = set(choices.group(1).split(","))

    for label, _ in cli._NOT_COVERED:
        command, subcommand = label.split()[:2]
        assert command == "agents"
        assert subcommand in subcommands, f"`{label}` is not an agents subcommand"
