"""The template agent must actually work.

`_template` is not in `registry.yaml` — it would otherwise show up in
`agents list` as if it were a real agent — so nothing else exercises it. A
template that quietly rotted would be worse than no template, because the
first thing anyone does with it is copy it.

These tests also double as the executable specification of the contract: if
you want to know what an agent must do, read them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

from orchestrator import manifest as manifest_mod
from orchestrator.contract import PROTOCOL, CallRequest
from orchestrator.discovery import load_registry, repo_root
from orchestrator.runner import call, describe

TEMPLATE = repo_root() / "_template"


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
    request = json.dumps(
        {"protocol": PROTOCOL, "capability": "describe", "input": {}}
    )
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
