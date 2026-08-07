"""The template agent must actually work.

`_template` is not in `registry.yaml` — it would otherwise show up in
`agents list` as if it were a real agent — so nothing else exercises it. A
template that quietly rotted would be worse than no template, because the
first thing anyone does with it is copy it.

These tests also double as the executable specification of the contract: if
you want to know what an agent must do, read them.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator import manifest as manifest_mod
from orchestrator.contract import PROTOCOL, CallRequest
from orchestrator.discovery import (
    AGENTS_DIR,
    SKIP_DIRS,
    TEMPLATE_DIR,
    TODO_MARKER,
    load_registry,
    repo_root,
)
from orchestrator.runner import call, describe

TEMPLATE = repo_root() / TEMPLATE_DIR


@pytest.fixture(scope="module")
def template():
    return manifest_mod.load(TEMPLATE)


def test_manifest_is_valid(template):
    assert template.name == "_template"
    assert "describe" in template.capability_names
    assert template.workdir == TEMPLATE


def test_describe_handshake(template):
    result = describe(template)
    assert result.ok, result.error
    assert result.output["name"] == "_template"
    assert result.output["protocol"] == PROTOCOL


def test_manifest_and_code_agree(template):
    """`agents describe` against `--static` — the drift check, automated."""
    live = {c["name"] for c in describe(template).output["capabilities"]}
    static = set(template.capability_names)
    assert live == static, f"agent.yaml and agent_main.py disagree: {live ^ static}"


def test_greet_round_trip(template):
    result = call(template, CallRequest(capability="greet", input={"name": "Dak"}))
    assert result.output["greeting"] == "Hello, Dak!"


def test_greet_rejects_bad_input(template):
    result = call(template, CallRequest(capability="greet", input={"name": ""}))
    assert result.error.type == "invalid_request"
    assert result.error.retryable is False


def test_unknown_capability_is_invalid_request(template):
    result = call(template, CallRequest(capability="nope"))
    assert result.error.type == "invalid_request"


def test_runs_without_the_orchestrator(template):
    """The self-containment claim, enforced rather than asserted in prose."""
    request = json.dumps({"protocol": PROTOCOL, "capability": "describe", "input": {}})
    proc = subprocess.run(
        [sys.executable, "agent_main.py"],
        cwd=TEMPLATE,
        input=request,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    # stdout must be exactly one JSON object — nothing else, no log lines.
    assert json.loads(proc.stdout)["ok"] is True


def test_template_is_not_registered():
    """Keeps `agents list` honest: a template is not an agent."""
    assert "_template" not in load_registry().agents


def _top_level_imports(source: Path) -> set[str]:
    """Root module of every import in `source`.

    Parsed, not matched. A line-based regex counts `import orchestrator`
    written inside a docstring — this file's own prose says it twice — and a
    boundary check that fires on prose is one people learn to ignore.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _sources(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if not SKIP_DIRS & set(p.relative_to(root).parts)]


def test_neither_side_imports_the_other():
    """The boundary CLAUDE.md calls load-bearing, checked rather than asserted.

    Both facts held when this was written and neither was tested — the rule in
    `.claude/rules/shared-code.md` said they were. The self-containment test
    above does not cover it: it runs the template under the root interpreter,
    where `orchestrator` is importable, so an agent that imported it would
    still pass.

    The second assertion looks redundant and is not. An orchestrator module
    importing a real agent package raises `ModuleNotFoundError` at collection,
    which is loud enough on its own — what this catches is the quiet version,
    where the agent has been installed into the root environment and the
    coupling therefore *works*.
    """
    root = repo_root()
    agents_dir = root / AGENTS_DIR

    offenders = [p for p in _sources(agents_dir) if "orchestrator" in _top_level_imports(p)]
    assert not offenders, f"an agent imports the orchestrator: {offenders}"

    # What an errant import would be spelled: an agent's top-level modules, and
    # the packages under its src/. Standard-library names are removed — an
    # agent shipping its own `json.py` would otherwise fail this for every
    # orchestrator module that imports `json`, and the orchestrator resolves
    # that name to the standard library regardless, the agent not being on its
    # path.
    agent_modules = (
        {p.stem for d in agents_dir.iterdir() if d.is_dir() for p in d.glob("*.py")}
        | {p.name for d in agents_dir.iterdir() if d.is_dir() for p in (d / "src").glob("*/")}
    ) - set(sys.stdlib_module_names)
    offenders = [
        p for p in _sources(root / "orchestrator") if agent_modules & _top_level_imports(p)
    ]
    assert not offenders, f"the orchestrator imports agent code: {offenders}"


def test_the_docstring_carries_a_marker():
    """Prose about the template needs the same tripwire as everything else.

    `agent.yaml`, the starter test, README.md and the body of `agent_main.py`
    all plant `TODO(new agent)`; the module docstring did not, and it is the
    one piece of prose here a reader takes at face value. "Standard library
    only, on purpose" is false the moment an agent adds a dependency, and on
    the last agent added here it survived two rounds of review still saying so.

    `integration_problems` already scans whole files, so the marker is the
    whole fix — which is exactly why it can be deleted by accident while the
    docstring is being reworded. This is what notices.
    """
    tree = ast.parse((TEMPLATE / "agent_main.py").read_text(encoding="utf-8"))
    docstring = ast.get_docstring(tree)

    assert docstring is not None, "agent_main.py lost its module docstring"
    assert TODO_MARKER in docstring, (
        "the template's module docstring has no TODO(new agent) marker, so a "
        "copy that leaves it verbatim passes `agents list --strict`"
    )


def test_a_copy_becomes_a_real_agent(tmp_path):
    """The documented copy-and-rename path, end to end.

    This is the instruction in _template/README.md executed literally. If the
    rename steps ever stop being sufficient, this fails before an intern
    discovers it.
    """
    shutil.copytree(TEMPLATE, tmp_path / "my-agent")
    agent = tmp_path / "my-agent"

    for file, old, new in (
        ("agent.yaml", "name: _template", "name: my-agent"),
        ("agent_main.py", 'AGENT_NAME = "_template"', 'AGENT_NAME = "my-agent"'),
    ):
        path = agent / file
        text = path.read_text(encoding="utf-8")
        assert old in text, f"{file}: rename anchor {old!r} is gone; update the README"
        path.write_text(text.replace(old, new), encoding="utf-8")

    (tmp_path / "registry.yaml").write_text(
        "version: 2\nagents:\n  - path: my-agent\n", encoding="utf-8"
    )

    registered = load_registry(tmp_path).get("my-agent")
    result = describe(registered)
    assert result.ok, result.error
    assert result.output["name"] == "my-agent"
