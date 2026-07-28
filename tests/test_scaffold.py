"""Scaffolding a new agent.

`agents new` exists because a contributor may not be using Claude Code, and
the four mechanical steps it replaces are the ones most often half-done: a
name changed in one of the two places that must agree, a folder never
registered, an agent missing from the README table.

What it must NOT do is finish the agent. The template's description, its
example capabilities, its `TODO(new agent)` markers and its absent `LICENSE`
all have to survive, because `agents list --strict` reporting them is how a
contributor learns the difference between a copy and an agent.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from orchestrator.cli import main
from orchestrator.discovery import integration_problems, load_registry
from orchestrator.runner import describe
from orchestrator.scaffold import ScaffoldError, create_agent

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A miniature repository: the real template, an empty registry, a README."""
    shutil.copytree(
        REPO / "_template",
        tmp_path / "_template",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (tmp_path / "registry.yaml").write_text("version: 2\n\nagents:\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "# Repo\n\n| Agent | Status | Capabilities |\n|---|---|---|\n\nAfter the table.\n",
        encoding="utf-8",
    )
    return tmp_path


def test_it_creates_a_registered_loadable_agent(repo: Path) -> None:
    create_agent(repo, "weather-agent")

    registry = load_registry(repo)
    assert "weather-agent" in registry.agents
    # The name lives in two files and discovery rejects them disagreeing.
    assert registry.get("weather-agent").name == "weather-agent"
    assert 'AGENT_NAME = "weather-agent"' in (repo / "weather-agent" / "agent_main.py").read_text()


def test_the_new_agent_answers_immediately(repo: Path) -> None:
    """Scaffolded, it must already satisfy the contract — just not mean anything."""
    create_agent(repo, "weather-agent")
    result = describe(load_registry(repo).get("weather-agent"))
    assert result.ok
    assert result.output["name"] == "weather-agent"


def test_it_is_registered_but_deliberately_not_integrated(repo: Path) -> None:
    """The whole point: `--strict` must still have something to teach.

    A scaffolder that filled in the description, invented capabilities and
    dropped in a LICENSE would produce an agent that passes the gate while
    meaning nothing — exactly what the gate exists to catch.
    """
    create_agent(repo, "weather-agent")
    problems = "\n".join(integration_problems(load_registry(repo)))

    assert "no LICENSE" in problems
    assert "TODO(new agent)" in problems
    assert "description is still the template's" in problems
    assert "only the template's example capabilities" in problems
    # ...but never these, which are the mechanical steps it did for you.
    assert "not in registry.yaml" not in problems
    assert "missing from the README.md agents table" not in problems


def test_the_readme_row_goes_inside_the_table(repo: Path) -> None:
    create_agent(repo, "weather-agent")
    lines = (repo / "README.md").read_text(encoding="utf-8").splitlines()
    row = next(i for i, line in enumerate(lines) if "weather-agent" in line)
    assert lines[row - 1].startswith("|"), "row must follow the table, not precede it"
    assert lines[row + 1].strip() == "", "row must be the last in the table"


def test_a_second_agent_lands_under_the_first(repo: Path) -> None:
    create_agent(repo, "aaa-agent")
    create_agent(repo, "zzz-agent")
    text = (repo / "README.md").read_text(encoding="utf-8")
    assert text.index("aaa-agent") < text.index("zzz-agent")
    assert len(load_registry(repo)) == 2


@pytest.mark.parametrize(
    "name",
    ["Weather", "weather agent", "weather_agent", "-weather", "weather-", "_hidden", "tests"],
)
def test_unusable_names_are_refused(repo: Path, name: str) -> None:
    with pytest.raises(ScaffoldError):
        create_agent(repo, name)
    assert not (repo / name).exists(), "a refused name must leave nothing behind"


def test_it_refuses_to_overwrite(repo: Path) -> None:
    create_agent(repo, "weather-agent")
    with pytest.raises(ScaffoldError, match="already exists"):
        create_agent(repo, "weather-agent")


def test_an_empty_registry_is_a_valid_state(repo: Path) -> None:
    """`agents:` with nothing under it parses as None, not a list.

    A repository with no agents yet is what a fresh clone of this scaffold
    looks like, and it has to load — otherwise `agents new` cannot be the
    first command anyone runs.
    """
    assert len(load_registry(repo)) == 0
    assert main(["--root", str(repo), "list"]) == 0


def test_registry_comments_survive(repo: Path) -> None:
    """Round-tripping through PyYAML would silently delete them."""
    (repo / "registry.yaml").write_text(
        "# Discovery is by declaration.\nversion: 2\n\nagents:\n", encoding="utf-8"
    )
    create_agent(repo, "weather-agent")
    assert "# Discovery is by declaration." in (repo / "registry.yaml").read_text(encoding="utf-8")


def test_cli_reports_each_step(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--root", str(repo), "new", "weather-agent"]) == 0
    out = capsys.readouterr().out
    assert "created weather-agent/" in out
    assert "registered weather-agent" in out
    # It must say what is deliberately left undone, or the omission reads as a bug.
    assert "LICENSE" in out


def test_cli_rejects_a_bad_name_without_creating_anything(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--root", str(repo), "new", "Weather Agent"]) == 2
    assert "not a usable agent name" in capsys.readouterr().err
    assert list(repo.glob("Weather*")) == []


def test_verify_fails_on_a_freshly_scaffolded_agent(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`verify` is the one command a contributor runs, so it must report the gap."""
    create_agent(repo, "weather-agent")
    assert main(["--root", str(repo), "verify", "weather-agent"]) == 1
    captured = capsys.readouterr()
    assert "FAIL  agents list --strict" in captured.out
    assert "gates failed" in captured.err


def test_verify_refuses_an_unknown_agent_name(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A misspelled name must be an error, not eight vacuous passes.

    `_verify_scope` used to drop unknown names silently, so
    `agents verify tpyo` verified nothing, printed "All 8 gates pass" and
    exited 0 — the exact wrong answer for the one command a contributor is
    told to trust.
    """
    create_agent(repo, "weather-agent")
    assert main(["--root", str(repo), "verify", "wether-agent"]) == 2
    captured = capsys.readouterr()
    assert "unknown agent 'wether-agent'" in captured.err
    assert "weather-agent" in captured.err, "the error must list what is registered"
    assert "gates pass" not in captured.out


def test_scaffold_refuses_a_registry_without_an_agents_key(repo: Path) -> None:
    """Appending to a registry with no `agents:` key used to corrupt it.

    The entry landed at top level, the file then parsed as something other
    than a mapping, every later command failed on it — and the scaffolder had
    already reported "registered" as a success step. Now it refuses up front,
    before creating anything.
    """
    (repo / "registry.yaml").write_text("# no agents key\nversion: 2\n", encoding="utf-8")
    with pytest.raises(ScaffoldError, match="no top-level `agents:` key"):
        create_agent(repo, "weather-agent")
    assert not (repo / "weather-agent").exists(), "must refuse before touching anything"
    assert "weather-agent" not in (repo / "registry.yaml").read_text(encoding="utf-8")


def test_scaffold_never_writes_a_registry_it_cannot_reload(repo: Path) -> None:
    """The textual append is parsed back before it is written.

    `agents:` not being the last key is the case the plain append gets wrong:
    the entry would land under the following key instead. The write must be
    refused and the file left byte-for-byte as it was.
    """
    original = "version: 2\nagents: []\nextra: value\n"
    (repo / "registry.yaml").write_text(original, encoding="utf-8")
    with pytest.raises(ScaffoldError, match="would not land under `agents:`"):
        create_agent(repo, "weather-agent")
    assert (repo / "registry.yaml").read_text(encoding="utf-8") == original
