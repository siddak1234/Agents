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
from orchestrator.contract import PROTOCOL, CallRequest, CallResult
from orchestrator.discovery import DiscoveryError, load_registry
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
    e = env(True, cap, {"name": "stub-agent", "protocol": P, "capabilities": []})
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
    (agent / "agent_main.py").write_text(STUB, encoding="utf-8")
    caps = "\n".join(f"  - name: {c}\n    description: {c}" for c in CAPS)
    (agent / "agent.yaml").write_text(
        textwrap.dedent(f"""\
            protocol: agentcall/v1
            name: stub-agent
            description: Stub agent for transport tests.
            runtime:
              type: subprocess
              command: ["{sys.executable}", "agent_main.py"]
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
