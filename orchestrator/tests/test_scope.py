"""`agents scope` — did this branch change only what it was entitled to?

The other gates ask whether an agent is well made. This one asks whether a
change stayed where it belongs, which is the question that matters when the
author is a contributor whose editor may have reformatted a file they never
meant to open, or who resolved a conflict by committing half the repository.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator.cli import main

REPO = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A miniature repository with a `main` to diff against."""
    (tmp_path / "agents" / "existing-agent").mkdir(parents=True)
    (tmp_path / "orchestrator").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "agents" / "existing-agent" / "agent.yaml").write_text("x\n", encoding="utf-8")
    (tmp_path / "orchestrator" / "runner.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "docs" / "CONTRIBUTING.md").write_text("x\n", encoding="utf-8")
    (tmp_path / "registry.yaml").write_text("version: 2\nagents:\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# x\n", encoding="utf-8")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-q", "-b", "work")
    return tmp_path


def _add(repo: Path, path: str, text: str = "x\n") -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _commit_and_scope(repo: Path) -> int:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")
    return main(["--root", str(repo), "scope", "--base", "main"])


def test_one_agent_plus_registry_readme_and_docs_is_allowed(repo: Path) -> None:
    """The shape of an honest contribution, including a docs typo fix."""
    _add(repo, "agents/parcel-geo/agent.yaml")
    _add(repo, "agents/parcel-geo/agent_main.py")
    _add(repo, "registry.yaml", "version: 2\nagents:\n  - path: agents/parcel-geo\n")
    _add(repo, "README.md", "# x\n| parcel-geo |\n")
    _add(repo, "docs/CONTRIBUTING.md", "x\ntypo fixed\n")

    assert _commit_and_scope(repo) == 0


def test_an_edit_to_shared_code_is_refused(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The case this exists for: a stray change to code every agent runs on.

    An editor that reformats on save produces exactly this diff, and it is the
    change a reviewer scanning a new agent is least likely to notice.
    """
    _add(repo, "agents/parcel-geo/agent.yaml")
    _add(repo, "orchestrator/runner.py", "x\n# reformatted by my editor\n")

    assert _commit_and_scope(repo) == 1
    err = capsys.readouterr().err
    assert "orchestrator/runner.py is outside your agent's folder" in err
    assert "its own pull request" in err, "the message must say what to do instead"


def test_touching_another_persons_agent_is_refused(repo: Path) -> None:
    """One agent per pull request — and never someone else's."""
    _add(repo, "agents/parcel-geo/agent.yaml")
    _add(repo, "agents/existing-agent/agent.yaml", "x\nedited\n")

    assert _commit_and_scope(repo) == 1


def test_the_blueprint_is_platform_work_not_an_agent(repo: Path) -> None:
    """`agents/_template` is the blueprint, not somebody's agent.

    A leading underscore means "not an agent" everywhere else in this repo —
    it is what keeps the template out of `agents list` — and it has to mean
    the same here, or every platform change that improves the blueprint is
    refused as a stray agent edit.
    """
    _add(repo, "agents/_template/agent_main.py", "x\n# a better blueprint\n")
    _add(repo, "orchestrator/runner.py", "x\n# and the platform change it serves\n")

    assert _commit_and_scope(repo) == 0


def test_a_platform_only_branch_is_not_this_commands_business(repo: Path) -> None:
    """Platform work is legitimate and reviewed on its own terms."""
    _add(repo, "orchestrator/runner.py", "x\n# real platform work\n")

    assert _commit_and_scope(repo) == 0


def test_a_branch_with_no_changes_passes(repo: Path) -> None:
    assert main(["--root", str(repo), "scope", "--base", "main"]) == 0


def test_outside_a_git_repository_it_skips_rather_than_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A check that could not run is not a check that found something.

    Blocking here would fail a contributor for their clone's depth rather than
    for anything they did.
    """
    (tmp_path / "registry.yaml").write_text("version: 2\nagents:\n", encoding="utf-8")

    assert main(["--root", str(tmp_path), "scope", "--base", "main"]) == 0
    assert "skipped" in capsys.readouterr().err


def test_an_unknown_base_ref_skips_rather_than_fails(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--root", str(repo), "scope", "--base", "no-such-ref"]) == 0
    assert "skipped" in capsys.readouterr().err
