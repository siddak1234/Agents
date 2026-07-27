"""Transport behaviour, pinned against a stub agent.

These cases are the ones that cost real debugging time when they regress:
a leaked stdout line, a crash with no envelope, a hang, a working directory
that is not the agent's own. Each is cheap to assert and expensive to
rediscover.

The stub is deliberately not `realty-lead-gen` — this file tests the
orchestrator, and binding it to a real agent's dependencies would make it
slow and make failures ambiguous.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from orchestrator import runner as runner_mod
from orchestrator.cli import main
from orchestrator.contract import PROTOCOL, CallRequest, CallResult
from orchestrator.discovery import DiscoveryError, load_registry, unregistered_agent_dirs
from orchestrator.runner import BASE_ENV, build_env, call, describe

STUB = """
import json, os, sys, time
P = "agentcall/v1"
def env(ok, cap, out=None, err=None):
    return {"protocol": P, "ok": ok, "capability": cap, "output": out,
            "usage": {"input_tokens": 1, "output_tokens": 2, "cost_micros": 3},
            "error": err}
real = sys.stdout
sys.stdout = sys.stderr
req = json.loads(sys.stdin.read() or "{}")
cap = req.get("capability", "")
if cap == "describe":
    # Must mirror the manifest exactly — `agents check` compares the two.
    e = env(True, cap, {"name": "stub-agent", "protocol": P,
                        "capabilities": [{"name": n, "description": n} for n in __CAPS__]})
elif cap == "echo":
    e = env(True, cap, {"echoed": req.get("input", {})})
elif cap == "where":
    e = env(True, cap, {"cwd": os.getcwd()})
elif cap == "environment":
    e = env(True, cap, {"keys": sorted(os.environ)})
elif cap == "flood":
    real.write("x" * int((req.get("input") or {}).get("bytes", 100000)))
    real.flush()
    sys.exit(0)
elif cap == "leak_stdout":
    real.write('{"level":"info","msg":"log line on stdout"}\\n')
    e = env(True, cap, {"fine": True})
elif cap == "crash":
    print("boom: simulated traceback", file=sys.stderr)
    sys.exit(3)
elif cap == "hang":
    time.sleep(30)
    e = env(True, cap, {})
else:
    e = env(False, cap, err={"type": "invalid_request", "message": "unknown capability"})
sys.stdout = real
json.dump(e, real)
real.write("\\n")
"""

CAPS = ["describe", "echo", "where", "environment", "flood", "leak_stdout", "crash", "hang"]


@pytest.fixture
def stub(tmp_path: Path):
    agent = tmp_path / "stub-agent"
    agent.mkdir()
    (agent / "agent_main.py").write_text(
        STUB.replace("__CAPS__", json.dumps(CAPS)), encoding="utf-8"
    )
    caps = "\n".join(f"  - name: {c}\n    description: {c}" for c in CAPS)
    (agent / "agent.yaml").write_text(
        textwrap.dedent(f"""\
            protocol: agentcall/v1
            name: stub-agent
            description: Stub agent for transport tests.
            runtime:
              type: subprocess
              command: ["{sys.executable}", "agent_main.py"]
              test: ["{sys.executable}", "-c", "pass"]
              env:
                inherit: [STUB_ALLOWED, STUB_PREFIXED_*]
            capabilities:
            """)
        + caps
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "registry.yaml").write_text(
        "version: 2\nagents:\n  - path: stub-agent\n", encoding="utf-8"
    )
    return load_registry(tmp_path).get("stub-agent")


def test_describe_round_trips(stub):
    result = describe(stub)
    assert result.ok
    assert result.output["name"] == "stub-agent"
    assert result.usage.cost_micros == 3


def test_input_reaches_the_agent(stub):
    result = call(stub, CallRequest(capability="echo", input={"a": 1}))
    assert result.output == {"echoed": {"a": 1}}


def test_cwd_is_the_agent_folder(stub):
    """The property that keeps an agent's relative paths (.env) resolving."""
    result = call(stub, CallRequest(capability="where"))
    assert Path(result.output["cwd"]) == stub.workdir


def test_stdout_leak_becomes_a_transport_error(stub):
    result = call(stub, CallRequest(capability="leak_stdout"))
    assert not result.ok
    assert result.error.type == "transport"
    # The message must point at the actual cause, not just "bad JSON".
    assert "stdout" in result.error.message


def test_crash_becomes_transport_error_with_stderr(stub):
    result = call(stub, CallRequest(capability="crash"))
    assert result.error.type == "transport"
    assert "exited 3" in result.error.message
    assert "boom" in result.error.message


def test_hang_becomes_retryable_timeout(stub):
    result = call(stub, CallRequest(capability="hang"), timeout_s=2)
    assert result.error.type == "timeout"
    assert result.error.retryable is True


def test_agent_reported_failure_is_not_a_transport_error(stub):
    result = call(stub, CallRequest(capability="nope"))
    assert not result.ok
    assert result.error.type == "invalid_request"


def test_registry_rejects_a_name_that_does_not_match_its_folder(tmp_path, stub):
    manifest = stub.workdir / "agent.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("name: stub-agent", "name: renamed"),
        encoding="utf-8",
    )
    with pytest.raises(DiscoveryError, match="does not match"):
        load_registry(stub.workdir.parent)


def test_registry_rejects_an_unregistered_folder(tmp_path, stub):
    (stub.workdir.parent / "not-registered").mkdir()
    registry = load_registry(stub.workdir.parent)
    assert "not-registered" not in registry.agents


def test_manifest_requires_describe(stub):
    manifest = stub.workdir / "agent.yaml"
    text = manifest.read_text(encoding="utf-8").replace(
        "  - name: describe\n    description: describe\n", ""
    )
    manifest.write_text(text, encoding="utf-8")
    with pytest.raises(DiscoveryError, match="describe"):
        load_registry(stub.workdir.parent)


def test_agent_sees_only_the_environment_it_declared(stub, monkeypatch):
    """Least privilege, enforced. An undeclared secret must not reach an agent.

    This is the test that would have caught the original implementation, which
    passed `os.environ` wholesale — meaning any agent could read every
    credential the orchestrator happened to hold.
    """
    monkeypatch.setenv("ORCHESTRATOR_SECRET", "do-not-leak")
    monkeypatch.setenv("STUB_ALLOWED", "declared")
    monkeypatch.setenv("STUB_PREFIXED_ONE", "declared by pattern")

    keys = set(call(stub, CallRequest(capability="environment")).output["keys"])

    assert "ORCHESTRATOR_SECRET" not in keys, "undeclared variable leaked to the agent"
    assert "STUB_ALLOWED" in keys
    assert "STUB_PREFIXED_ONE" in keys
    assert "PATH" in keys, "base variables must still be present or nothing can run"


def test_inherit_everything_is_rejected(stub):
    manifest = stub.workdir / "agent.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "inherit: [STUB_ALLOWED, STUB_PREFIXED_*]", 'inherit: ["*"]'
        ),
        encoding="utf-8",
    )
    with pytest.raises(DiscoveryError, match="may not be"):
        load_registry(stub.workdir.parent)


def test_env_defaults_to_nothing(tmp_path, stub):
    """A manifest that declares no env inherits none — silence is not consent."""
    manifest = stub.workdir / "agent.yaml"
    kept = [
        line
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if "env:" not in line and "inherit:" not in line
    ]
    manifest.write_text("\n".join(kept) + "\n", encoding="utf-8")

    reloaded = load_registry(stub.workdir.parent).get("stub-agent")
    assert reloaded.env.inherit == ()
    assert set(build_env(reloaded)) <= set(BASE_ENV)


def test_runaway_stdout_is_capped(stub, monkeypatch):
    """A looping agent must not take the orchestrator's memory with it."""
    monkeypatch.setattr(runner_mod, "MAX_OUTPUT_BYTES", 1024)
    result = call(stub, CallRequest(capability="flood", input={"bytes": 50_000}))
    assert result.error.type == "transport"
    assert "over the" in result.error.message and "limit" in result.error.message


def test_agents_test_runs_the_declared_command_in_the_agent_folder(stub, capsys):
    assert main(["--root", str(stub.workdir.parent), "test"]) == 0
    assert "ok    stub-agent" in capsys.readouterr().out


def test_an_agent_declaring_no_test_command_fails(stub, capsys):
    """Tests nothing can run are tests nobody runs."""
    manifest = stub.workdir / "agent.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            f'  test: ["{sys.executable}", "-c", "pass"]\n', ""
        ),
        encoding="utf-8",
    )
    assert main(["--root", str(stub.workdir.parent), "test"]) == 1
    assert "declares no runtime.test" in capsys.readouterr().err


def test_the_test_command_gets_the_same_environment_a_call_gets(stub, monkeypatch):
    """Deny by default has to cover `agents test`, not just `agents call`.

    A test command is arbitrary contributed code, run on a maintainer's machine
    and in CI where the real secrets are. Handing it `os.environ` wholesale —
    which is what a bare `subprocess.run` does — is a wider grant than the
    agent gets when it is actually called.
    """
    monkeypatch.setenv("ORCHESTRATOR_SECRET", "do-not-leak")
    monkeypatch.setenv("STUB_ALLOWED", "declared")

    probe = stub.workdir / "probe.py"
    probe.write_text(
        "import os, sys\n"
        "sys.exit(7 if 'ORCHESTRATOR_SECRET' in os.environ else\n"
        "         0 if os.environ.get('STUB_ALLOWED') == 'declared' else 8)\n",
        encoding="utf-8",
    )
    manifest = stub.workdir / "agent.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            f'  test: ["{sys.executable}", "-c", "pass"]',
            f'  test: ["{sys.executable}", "probe.py"]',
        ),
        encoding="utf-8",
    )

    # 7 = the undeclared secret reached it; 8 = the declared one did not.
    assert main(["--root", str(stub.workdir.parent), "test"]) == 0


def test_check_can_be_scoped_to_named_agents(stub, capsys):
    """CI names the agents a change touched; unknown names must fail loudly.

    Without this, a typo in a workflow silently checks nothing and the job
    goes green having verified an agent that does not exist.
    """
    root = str(stub.workdir.parent)
    assert main(["--root", root, "check", "stub-agent"]) == 0
    assert "stub-agent" in capsys.readouterr().out

    assert main(["--root", root, "check", "no-such-agent"]) == 2
    assert "unknown agent" in capsys.readouterr().err


def test_check_with_no_names_covers_every_agent(stub, capsys):
    assert main(["--root", str(stub.workdir.parent), "check"]) == 0
    assert "stub-agent" in capsys.readouterr().out


def test_an_unregistered_agent_folder_is_caught(stub, capsys):
    """The half-finished integration: agent written, registry step forgotten.

    Nothing else notices — discovery ignores unregistered folders on purpose —
    so without this the agent merges and is simply never callable.
    """
    root = stub.workdir.parent
    orphan = root / "forgotten-agent"
    orphan.mkdir()
    (orphan / "agent.yaml").write_text("protocol: agentcall/v1\n", encoding="utf-8")

    assert unregistered_agent_dirs(load_registry(root)) == ["forgotten-agent"]

    assert main(["--root", str(root), "list"]) == 0  # tolerated by default
    assert main(["--root", str(root), "list", "--strict"]) == 1  # rejected in CI
    assert "forgotten-agent" in capsys.readouterr().err


def test_underscore_folders_are_not_agents(stub):
    """`_template` carries an agent.yaml and must never count as an agent."""
    (stub.workdir.parent / "_scratch").mkdir()
    (stub.workdir.parent / "_scratch" / "agent.yaml").write_text("x: 1\n", encoding="utf-8")
    assert unregistered_agent_dirs(load_registry(stub.workdir.parent)) == []


def test_envelope_survives_a_round_trip():
    wire = json.dumps(
        {
            "protocol": PROTOCOL,
            "ok": True,
            "capability": "x",
            "output": {"n": 1},
            "usage": {"input_tokens": 5, "output_tokens": 6, "cost_micros": 7},
            "error": None,
        }
    )
    assert CallResult.decode(wire, capability="x").usage.input_tokens == 5
