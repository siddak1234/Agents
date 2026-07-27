"""`agents list --strict` — the difference between registered and integrated.

Discovery proves an agent loads and answers. That is a low bar: copy
`_template`, change the name in two places, and every gate passes while the
agent still describes itself as a template and offers only the example
capability. These tests pin the checks that tell the two apart.

Written from the scenario they exist for — someone contributing their first
agent — so the first test is literally that copy-and-rename, and it must fail.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from orchestrator.cli import main
from orchestrator.discovery import integration_problems, load_registry

TEMPLATE_MANIFEST = """\
protocol: agentcall/v1
name: _template
description: Template agent — copy this folder to start a new one.
runtime:
  type: subprocess
  # TODO(new agent): point this at your own environment.
  command: ["python3", "agent_main.py"]
capabilities:
  - name: describe
    description: Report this agent's name and capabilities.
  - name: greet
    description: Return a greeting. Replace this with something useful.
"""


def _repo(tmp_path: Path) -> Path:
    """A repository holding the template and nothing else."""
    template = tmp_path / "_template"
    template.mkdir()
    (template / "agent.yaml").write_text(TEMPLATE_MANIFEST, encoding="utf-8")
    (template / "README.md").write_text("# Template agent\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Agents\n\n| Agent | Status |\n", encoding="utf-8")
    (tmp_path / "registry.yaml").write_text("version: 2\nagents: []\n", encoding="utf-8")
    return tmp_path


def _copy_template_as(root: Path, name: str) -> Path:
    """Exactly what CONTRIBUTING tells a contributor to do: copy and rename."""
    agent = root / name
    agent.mkdir()
    (agent / "agent.yaml").write_text(
        TEMPLATE_MANIFEST.replace("name: _template", f"name: {name}"), encoding="utf-8"
    )
    (agent / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    (root / "registry.yaml").write_text(
        f"version: 2\nagents:\n  - path: {name}\n", encoding="utf-8"
    )
    return agent


def test_a_renamed_template_is_not_an_integrated_agent(tmp_path: Path) -> None:
    """The scenario this whole check exists for. It must not pass."""
    root = _repo(tmp_path)
    _copy_template_as(root, "weather-agent")

    problems = "\n".join(integration_problems(load_registry(root)))

    assert "no LICENSE" in problems
    assert "TODO(new agent)" in problems
    assert "description is still the template's" in problems
    assert "only the template's example capabilities" in problems
    assert "missing from the README.md agents table" in problems


def test_strict_exits_non_zero_and_says_why(tmp_path: Path, capsys) -> None:
    root = _repo(tmp_path)
    _copy_template_as(root, "weather-agent")

    assert main(["--root", str(root), "list", "--strict"]) == 1
    assert "not yet an agent that is integrated" in capsys.readouterr().err

    # Without --strict it still lists: loading is a separate question.
    assert main(["--root", str(root), "list"]) == 0


def test_a_finished_integration_passes(tmp_path: Path) -> None:
    """Work through everything the check reports, and it goes quiet."""
    root = _repo(tmp_path)
    agent = _copy_template_as(root, "weather-agent")

    (agent / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (agent / "agent.yaml").write_text(
        textwrap.dedent("""\
            protocol: agentcall/v1
            name: weather-agent
            description: Forecasts and severe-weather alerts for a coordinate.
            runtime:
              type: subprocess
              command: ["python3", "agent_main.py"]
            capabilities:
              - name: describe
                description: Report this agent's name and capabilities.
              - name: forecast
                description: Seven-day forecast for a latitude and longitude.
            """),
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "# Agents\n\n| Agent |\n|---|\n| weather-agent |\n", encoding="utf-8"
    )

    assert integration_problems(load_registry(root)) == []


@pytest.mark.parametrize(
    ("removed", "expected"),
    [("LICENSE", "no LICENSE"), ("README.md", "no README.md")],
)
def test_each_required_file_is_reported_by_name(
    tmp_path: Path, removed: str, expected: str
) -> None:
    root = _repo(tmp_path)
    agent = _copy_template_as(root, "weather-agent")
    (agent / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (agent / removed).unlink()

    assert any(expected in p for p in integration_problems(load_registry(root)))


def test_the_template_itself_is_never_checked(tmp_path: Path) -> None:
    """`_template` is unregistered by design, so its own TODOs are fine."""
    root = _repo(tmp_path)
    assert integration_problems(load_registry(root)) == []
