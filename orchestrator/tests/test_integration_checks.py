"""`agents list --strict` — the difference between registered and integrated.

Discovery proves an agent loads and answers. That is a low bar: copy
`_template`, change the name in two places, and every gate passes while the
agent still describes itself as a template and offers only the example
capability. These tests pin the checks that tell the two apart.

Written from the scenario they exist for — someone contributing their first
agent — so the first test is literally that copy-and-rename, and it must fail.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest
import yaml

from orchestrator.cli import main
from orchestrator.discovery import (
    TEMPLATE_DIR,
    TODO_MARKER,
    _has_readme_row,
    integration_problems,
    load_registry,
)

#: The real template, not a transcript of it. An earlier version of this file
#: hardcoded a copy of agent.yaml, which drifted the moment the template
#: gained `runtime.test` and `runtime.lint` — leaving the flagship test
#: asserting two problems a genuinely renamed template can no longer produce.
TEMPLATE_SRC = Path(__file__).resolve().parents[2] / TEMPLATE_DIR

#: Read from the template so an edit there cannot silently invalidate the
#: description-similarity tests below.
TEMPLATE_DESCRIPTION: str = yaml.safe_load((TEMPLATE_SRC / "agent.yaml").read_text())["description"]


def _repo(tmp_path: Path) -> Path:
    """A repository holding the real template and nothing else."""
    shutil.copytree(
        TEMPLATE_SRC,
        tmp_path / TEMPLATE_DIR,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (tmp_path / "README.md").write_text("# Agents\n\n| Agent | Status |\n", encoding="utf-8")
    (tmp_path / "registry.yaml").write_text("version: 2\nagents: []\n", encoding="utf-8")
    return tmp_path


def _copy_template_as(root: Path, name: str) -> Path:
    """Exactly what CONTRIBUTING tells a contributor to do: copy and rename.

    The whole folder, and the name changed in the two places that must agree —
    nothing else. This is the minimum that makes discovery load the agent, and
    the point of these tests is that the minimum is not integration.
    """
    agent = root / "agents" / name
    agent.parent.mkdir(exist_ok=True)
    shutil.copytree(root / TEMPLATE_DIR, agent)
    manifest = agent / "agent.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("name: _template", f"name: {name}", 1),
        encoding="utf-8",
    )
    entry = agent / "agent_main.py"
    entry.write_text(
        entry.read_text(encoding="utf-8").replace(
            'AGENT_NAME = "_template"', f'AGENT_NAME = "{name}"', 1
        ),
        encoding="utf-8",
    )
    (root / "registry.yaml").write_text(
        f"version: 2\nagents:\n  - path: agents/{name}\n", encoding="utf-8"
    )
    return agent


def _clear_markers(agent: Path) -> None:
    """Work through every TODO marker the template plants, wherever it is."""
    for path in agent.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            if TODO_MARKER in text:
                path.write_text(text.replace(TODO_MARKER, "done"), encoding="utf-8")


def test_a_registered_agent_outside_agents_dir_is_told_to_move(tmp_path: Path) -> None:
    """The layout rule: agents live in agents/<name>, and the gate says so.

    A registered path anywhere else still loads — discovery is by
    declaration — but it puts tenant code in platform space, and the next
    contributor copies whatever the last one did.
    """
    root = _repo(tmp_path)
    agent = root / "weather-agent"
    shutil.copytree(root / TEMPLATE_DIR, agent)
    manifest = agent / "agent.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("name: _template", "name: weather-agent", 1),
        encoding="utf-8",
    )
    (root / "registry.yaml").write_text(
        "version: 2\nagents:\n  - path: weather-agent\n", encoding="utf-8"
    )

    problems = "\n".join(integration_problems(load_registry(root)))
    assert "outside agents/" in problems
    assert "agents/<name>" in problems


def test_a_stray_agent_folder_at_the_root_is_reported(tmp_path: Path) -> None:
    """An unregistered agent.yaml at the root is caught, not just in agents/.

    That is where an old habit or a copied tutorial puts one, and discovery
    ignoring it by design is exactly why the gate must not.
    """
    root = _repo(tmp_path)
    _copy_template_as(root, "weather-agent")  # a properly-placed agent too
    stray = root / "parcel-geo"
    stray.mkdir()
    shutil.copy(root / TEMPLATE_DIR / "agent.yaml", stray / "agent.yaml")

    problems = "\n".join(integration_problems(load_registry(root)))
    assert "parcel-geo/ holds an agent.yaml but is not in registry.yaml" in problems
    assert "path: agents/parcel-geo" in problems, "the fix must name the right location"


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

    # The template ships real starter declarations for both of these, so a
    # renamed copy must NOT be told they are missing — that message is for
    # agents that deleted them, covered separately below.
    assert "no runtime.test" not in problems
    assert "no runtime.lint" not in problems


def test_deleting_test_or_lint_commands_is_reported(tmp_path: Path) -> None:
    """The template declares both; an agent that drops either is told so."""
    root = _repo(tmp_path)
    agent = _copy_template_as(root, "weather-agent")
    manifest = agent / "agent.yaml"
    kept = [
        line
        for line in manifest.read_text(encoding="utf-8").splitlines(keepends=True)
        if not line.strip().startswith(("test:", "lint:"))
    ]
    manifest.write_text("".join(kept), encoding="utf-8")

    problems = "\n".join(integration_problems(load_registry(root)))
    assert "no runtime.test" in problems
    assert "no runtime.lint" in problems


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
              test: ["python3", "-m", "unittest", "discover", "-s", "tests"]
              lint: ["python3", "-m", "compileall", "-q", "."]
            capabilities:
              - name: describe
                description: Report this agent's name and capabilities.
                input_schema: { type: object, properties: {} }
                output_schema: { type: object, required: [name] }
              - name: forecast
                description: Seven-day forecast for a latitude and longitude.
                input_schema: { type: object, required: [lat, lon] }
                output_schema: { type: object, required: [days] }
            """),
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "# Agents\n\n| Agent |\n|---|\n| weather-agent |\n", encoding="utf-8"
    )
    # The template plants markers outside the manifest too, and the folder is
    # scanned whole — so finishing the integration means clearing all of them.
    _clear_markers(agent)

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


def _finished(root: Path, name: str = "weather-agent") -> Path:
    """An agent with every reported problem worked through."""
    agent = _copy_template_as(root, name)
    (agent / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (agent / "agent.yaml").write_text(
        textwrap.dedent(f"""\
            protocol: agentcall/v1
            name: {name}
            description: Forecasts and severe-weather alerts for a coordinate.
            runtime:
              type: subprocess
              command: ["python3", "agent_main.py"]
              test: ["python3", "-c", "pass"]
              lint: ["python3", "-c", "pass"]
            capabilities:
              - name: describe
                description: Report this agent's name and capabilities.
                input_schema: {{ type: object }}
                output_schema: {{ type: object }}
              - name: forecast
                description: Seven-day forecast for a coordinate.
                input_schema: {{ type: object }}
                output_schema: {{ type: object }}
            """),
        encoding="utf-8",
    )
    _clear_markers(agent)
    (root / "README.md").write_text(f"# Agents\n\n| Agent |\n|---|\n| {name} |\n", encoding="utf-8")
    return agent


def test_a_todo_marker_outside_the_manifest_is_caught(tmp_path: Path) -> None:
    """The manifest-only scan missed most of the template's own markers.

    The template plants them in `agent_main.py`, its README and its starter
    test as well, so a copy could clear `agent.yaml` alone and pass.
    """
    root = _repo(tmp_path)
    agent = _finished(root)
    assert integration_problems(load_registry(root)) == []

    (agent / "agent_main.py").write_text(f"# {TODO_MARKER}: implement this\n", encoding="utf-8")
    problems = "\n".join(integration_problems(load_registry(root)))
    assert TODO_MARKER in problems
    assert "agent_main.py" in problems, "the message must name the file to edit"


def test_a_capability_without_schemas_is_caught(tmp_path: Path) -> None:
    """A missing schema silently defaulted to `{}` and documented nothing."""
    root = _repo(tmp_path)
    agent = _finished(root)
    manifest = agent / "agent.yaml"
    kept = [
        line
        for line in manifest.read_text(encoding="utf-8").splitlines(keepends=True)
        if "input_schema" not in line
    ]
    manifest.write_text("".join(kept), encoding="utf-8")

    problems = "\n".join(integration_problems(load_registry(root)))
    assert "declares no input_schema" in problems
    assert "declares no output_schema" not in problems, "only the removed one should report"


def test_a_lightly_edited_template_description_is_caught(tmp_path: Path) -> None:
    """Exact-string comparison was defeated by changing one character."""
    root = _repo(tmp_path)
    agent = _finished(root)
    manifest = agent / "agent.yaml"
    template_description = TEMPLATE_DESCRIPTION

    for edited in (
        template_description,
        template_description.rstrip(".") + "!",
        template_description.upper(),
        "  " + template_description + "  ",
        template_description + " now",
    ):
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                manifest.read_text(encoding="utf-8").split("description: ")[1].split("\n")[0],
                edited,
            ),
            encoding="utf-8",
        )
        problems = "\n".join(integration_problems(load_registry(root)))
        assert "description is still the template's" in problems, f"{edited!r} slipped through"


def test_the_template_sentence_padded_with_filler_is_still_caught(tmp_path: Path) -> None:
    """Ratio alone was diluted by length, so padding beat it.

    `SequenceMatcher.ratio()` divides by combined length: keeping the template
    sentence verbatim and appending two lines of unrelated prose scored 0.38
    and passed — an easier evasion than the one-character edit the ratio was
    added to catch.
    """
    root = _repo(tmp_path)
    agent = _finished(root)
    manifest = agent / "agent.yaml"
    padded = (
        f"{TEMPLATE_DESCRIPTION} This agent processes real estate leads and grades "
        f"photos for condition, and does a great deal of other useful work."
    )
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "Forecasts and severe-weather alerts for a coordinate.", padded
        ),
        encoding="utf-8",
    )
    assert any(
        "description is still the template's" in p
        for p in integration_problems(load_registry(root))
    )


def test_a_row_for_a_different_agent_does_not_satisfy_a_substring_name(
    tmp_path: Path,
) -> None:
    """Narrowing to `|` rows kept a substring test, so `gen` matched `lead-gen`.

    Any agent whose name is contained in an existing agent's name would never
    need a row of its own.
    """
    root = _repo(tmp_path)
    _finished(root, "parcel-geo")
    (root / "README.md").write_text(
        "# Agents\n\n| Agent |\n|---|\n| [`parcel-geo`](./parcel-geo) |\n", encoding="utf-8"
    )
    assert integration_problems(load_registry(root)) == []

    # A second agent whose name is a substring of the first must not be
    # satisfied by the first's row.
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert _has_readme_row(readme, "parcel-geo")
    assert not _has_readme_row(readme, "geo")
    assert not _has_readme_row(readme, "parcel")


def test_a_mention_outside_the_table_is_not_a_readme_row(tmp_path: Path) -> None:
    """A bare substring search passed on any mention anywhere in the file."""
    root = _repo(tmp_path)
    _finished(root)

    (root / "README.md").write_text(
        "# Agents\n\nSomeday weather-agent will be documented properly.\n", encoding="utf-8"
    )
    assert any(
        "missing from the README.md agents table" in p
        for p in integration_problems(load_registry(root))
    )

    (root / "README.md").write_text(
        "# Agents\n\n| Agent |\n|---|\n| weather-agent |\n", encoding="utf-8"
    )
    assert integration_problems(load_registry(root)) == []


def test_a_missing_template_is_reported_rather_than_skipping_checks(tmp_path: Path) -> None:
    """Deleting `_template/` silently disabled two checks."""
    root = _repo(tmp_path)
    _finished(root)
    assert integration_problems(load_registry(root)) == []

    (root / TEMPLATE_DIR / "agent.yaml").unlink()
    problems = "\n".join(integration_problems(load_registry(root)))
    assert "_template/ is missing or unreadable" in problems


def test_the_marker_scan_ignores_caches_and_virtualenvs(tmp_path: Path) -> None:
    """An agent's `.venv` can hold thousands of files it did not write."""
    root = _repo(tmp_path)
    agent = _finished(root)
    for noise in (".venv/lib/thing.py", "__pycache__/x.py", "node_modules/y.js"):
        path = agent / noise
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {TODO_MARKER}\n", encoding="utf-8")

    assert integration_problems(load_registry(root)) == []
