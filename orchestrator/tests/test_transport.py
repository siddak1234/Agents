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
import yaml

from orchestrator import runner as runner_mod
from orchestrator.cli import main
from orchestrator.contract import PROTOCOL, CallRequest, CallResult, ProtocolError
from orchestrator.discovery import DiscoveryError, load_registry, unregistered_agent_dirs
from orchestrator.manifest import AgentEnv, ManifestError
from orchestrator.runner import BASE_ENV, build_env, call, describe

STUB = """
import json, os, sys, time
P = "agentcall/v1"
def env(ok, cap, out=None, err=None):
    return {"protocol": P, "ok": ok, "capability": cap, "output": out,
            "usage": {"input_tokens": 1, "output_tokens": 2, "model": "test-model"},
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


def _yaml_scalar(text: str) -> str:
    """Escape `text` for interpolation into a double-quoted YAML scalar.

    A double-quoted scalar processes escape sequences, so a Windows
    interpreter path — `C:\\Users\\...\\python.exe` — makes `\\U` the start of a
    32-bit unicode escape and the manifest fails to parse before any test
    runs. JSON string escaping is a subset of YAML's, so `json.dumps` produces
    a scalar that round-trips on every platform; on POSIX it is a no-op.
    """
    return json.dumps(text)[1:-1]


EXE = _yaml_scalar(sys.executable)


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
              command: ["{EXE}", "agent_main.py"]
              test: ["{EXE}", "-c", "pass"]
              lint: ["{EXE}", "-c", "pass"]
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
    assert result.usage.model == "test-model"


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


def test_the_handshake_receives_no_inherited_environment(stub, monkeypatch):
    """`agents check` is documented "no credentials" — this makes it true.

    `describe` used to run through the same environment as any capability
    call, so the handshake received every variable the manifest inherits —
    keys included, on every `agents check`, in every CI run. The handshake
    proves the entrypoint resolves and the manifest matches the code; none of
    that may depend on a credential.
    """
    monkeypatch.setenv("STUB_ALLOWED", "a-credential")

    assert "STUB_ALLOWED" in build_env(stub)
    assert "STUB_ALLOWED" not in build_env(stub, inherit=False)

    # End to end: the subprocess itself must not see it either.
    keys = set(call(stub, CallRequest(capability="environment"), inherit_env=False).output["keys"])
    assert "STUB_ALLOWED" not in keys
    assert "PATH" in keys

    assert describe(stub).ok, "describe must still answer with the inherited env withheld"


def _with_inherit(stub, entry: str):
    """Rewrite the stub's inherit list to a single entry and reload."""
    manifest = stub.workdir / "agent.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "inherit: [STUB_ALLOWED, STUB_PREFIXED_*]", f"inherit: [{entry}]"
        ),
        encoding="utf-8",
    )
    return load_registry(stub.workdir.parent)


#: Every one of these matched essentially the whole environment through
#: `fnmatch` while passing an equality check against `"*"`. They are the
#: reason the rule is a shape rule rather than a denylist. `"AB*"` is the
#: boundary case: without it, lowering MIN_INHERIT_PREFIX to 2 breaks nothing
#: in this file and the threshold stops being pinned by anything.
WILDCARD_EVASIONS = [
    '"*"',
    '"?*"',
    '"**"',
    '"*_*"',
    '"[A-Z]*"',
    '"?"',
    '"*KEY*"',
    '"A*"',
    '"AB*"',
]


@pytest.mark.parametrize("entry", WILDCARD_EVASIONS)
def test_inherit_cannot_smuggle_in_the_whole_environment(stub, entry):
    """Deny-by-default has to survive someone writing "everything" differently.

    `build_env` matches with `fnmatch`, which reads `?` and `[…]` too — so
    rejecting the literal `"*"` and nothing else left `?*`, `**`, `*_*` and
    `[A-Z]*` each matching every variable the orchestrator holds. A short
    prefix is the same problem more quietly: `A*` reaches
    `AWS_SECRET_ACCESS_KEY`.
    """
    with pytest.raises(DiscoveryError):
        _with_inherit(stub, entry)


@pytest.mark.parametrize(
    "entry",
    [
        "STUB_ALLOWED",
        "STUB_PREFIXED_*",
        "ANTHROPIC_MODEL_*",
        # The other half of the boundary: exactly MIN_INHERIT_PREFIX characters
        # must still be accepted, so raising the threshold also fails a test
        # rather than silently narrowing what agents may declare.
        "ABC*",
    ],
)
def test_an_exact_name_or_a_real_prefix_is_still_allowed(stub, entry):
    """The rule must not break the two shapes agents legitimately use."""
    assert _with_inherit(stub, entry).get("stub-agent").env.inherit == (entry,)


def test_the_inherit_rule_is_enforced_by_the_type_not_only_the_loader():
    """A future construction path must not be able to skip the check.

    `build_env` consumes `inherit` a long way from where the loader validated
    it, and this invariant is what stops an agent reading a credential it never
    declared — so it belongs on the type, not only in the one function that
    happens to build it today.
    """
    with pytest.raises(ManifestError, match="at most one"):
        AgentEnv(inherit=("*_*",))
    assert AgentEnv(inherit=("STUB_PREFIXED_*", "EXACT_NAME")).inherit == (
        "STUB_PREFIXED_*",
        "EXACT_NAME",
    )


def test_a_wildcard_entry_does_not_reach_an_undeclared_secret(stub, monkeypatch):
    """The property the shape rule exists to protect, asserted end to end."""
    monkeypatch.setenv("SUPER_SECRET_DB_PASSWORD", "hunter2")
    monkeypatch.setenv("STUB_PREFIXED_ONE", "declared by pattern")

    reloaded = _with_inherit(stub, "STUB_PREFIXED_*")
    keys = set(build_env(reloaded.get("stub-agent")))

    assert "STUB_PREFIXED_ONE" in keys
    assert "SUPER_SECRET_DB_PASSWORD" not in keys


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


def test_agents_lint_runs_the_declared_command(stub, capsys):
    """Root tooling covers root-owned code only, so this is the only thing
    that checks a contributed agent's source."""
    assert main(["--root", str(stub.workdir.parent), "lint"]) == 0
    assert "ok    stub-agent" in capsys.readouterr().out


def test_an_agent_declaring_no_lint_command_fails(stub, capsys):
    manifest = stub.workdir / "agent.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(f'  lint: ["{EXE}", "-c", "pass"]\n', ""),
        encoding="utf-8",
    )
    assert main(["--root", str(stub.workdir.parent), "lint"]) == 1
    assert "declares no runtime.lint" in capsys.readouterr().err


def test_an_agent_declaring_no_test_command_fails(stub, capsys):
    """Tests nothing can run are tests nobody runs."""
    manifest = stub.workdir / "agent.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(f'  test: ["{EXE}", "-c", "pass"]\n', ""),
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
            f'  test: ["{EXE}", "-c", "pass"]',
            f'  test: ["{EXE}", "probe.py"]',
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


def _envelope(**overrides) -> str:
    body = {
        "protocol": PROTOCOL,
        "ok": True,
        "capability": "x",
        "output": {"n": 1},
        "usage": {"input_tokens": 5, "output_tokens": 6, "model": "test-model"},
        "error": None,
    }
    body.update(overrides)
    return json.dumps({k: v for k, v in body.items() if v is not _OMIT})


_OMIT = object()


def test_envelope_survives_a_round_trip():
    assert CallResult.decode(_envelope(), capability="x").usage.input_tokens == 5


@pytest.mark.parametrize("bad", [_OMIT, None, 7, "none", []])
def test_usage_is_mandatory_not_merely_documented(bad):
    """docs/AGENT_PROTOCOL.md says usage is always present. Now it is enforced.

    A missing or non-object `usage` silently decoded as zeros, so an agent could
    omit accounting entirely and its envelope still read as valid. Zeros an
    agent declared and zeros the orchestrator invented are different claims.
    """
    with pytest.raises(ProtocolError, match="usage"):
        CallResult.decode(_envelope(usage=bad), capability="x")


@pytest.mark.parametrize(
    "value",
    [
        "many",
        "5",  # a numeric string was silently coerced
        1.5,
        True,  # bool is an int subclass, so `int()` turned True into 1 token
        None,
        [],
    ],
)
def test_a_non_integer_usage_field_is_rejected_rather_than_coerced(value):
    with pytest.raises(ProtocolError, match="must be an integer"):
        CallResult.decode(_envelope(usage={"input_tokens": value}), capability="x")


def test_a_negative_usage_count_is_rejected():
    """A negative count would quietly corrupt any total built on top of it."""
    with pytest.raises(ProtocolError, match="must not be negative"):
        CallResult.decode(_envelope(usage={"input_tokens": -1}), capability="x")


def test_usage_reports_the_model_not_money():
    """The contract carries `model`; a stale `cost_micros` is not a model.

    An agent that never migrated still decodes -- it just reports having
    called no model, which is what `model: null` says. The failure mode is
    an honest absence, not a wrong number, which is the whole point of the
    change (docs/AGENT_PROTOCOL.md).
    """
    stale = CallResult.decode(
        _envelope(usage={"input_tokens": 9, "output_tokens": 3, "cost_micros": 4200}),
        capability="x",
    )
    assert stale.usage.model is None
    assert stale.usage.input_tokens == 9
    assert not hasattr(stale.usage, "cost_micros")


def test_usage_model_must_be_a_string_or_null():
    with pytest.raises(ProtocolError, match=r"usage\.model"):
        CallResult.decode(
            _envelope(usage={"input_tokens": 0, "output_tokens": 0, "model": 7}),
            capability="x",
        )


def test_usage_may_report_zeros_when_nothing_was_spent():
    """The whole point of mandatory accounting is that zero is a real answer."""
    result = CallResult.decode(_envelope(usage={}), capability="x")
    assert (result.usage.input_tokens, result.usage.model) == (0, None)


def test_an_agent_cannot_emit_a_transport_error():
    """`transport` is the orchestrator's word, and only the orchestrator's.

    docs/AGENT_PROTOCOL.md says agents never emit it, but because it sat in
    ERROR_TYPES an agent could — and its envelope was indistinguishable from
    an orchestrator-side failure, which is the one distinction the taxonomy
    exists to hold. An agent-emitted `transport` now demotes to `internal`
    with the original text preserved.
    """
    raw = _envelope(ok=False, output=None, error={"type": "transport", "message": "spoofed"})
    result = CallResult.decode(raw, capability="x")
    assert result.error is not None
    assert result.error.type == "internal"
    assert "transport" in result.error.message
    assert "spoofed" in result.error.message, "the agent's own text must survive"


def test_a_windows_interpreter_path_still_yields_a_parseable_manifest() -> None:
    """The stub manifest has to parse whatever `sys.executable` looks like.

    Interpolated raw into a double-quoted scalar, a Windows interpreter path
    fails on `\\U`, and every test in this file would fail on Windows and
    nowhere else — a whole platform's worth of coverage lost to a quoting bug
    in a fixture. Reported by @ankur-15.
    """
    windows = r"C:\Users\a\AppData\Local\Programs\Python\Python313\python.exe"

    parsed = yaml.safe_load(f'command: ["{_yaml_scalar(windows)}", "agent_main.py"]\n')
    assert parsed["command"][0] == windows

    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(f'command: ["{windows}", "agent_main.py"]\n')


def test_a_stale_usage_field_is_named_rather_than_ignored():
    """Lenient decode, loud gate.

    `test_usage_reports_the_model_not_money` fixes the runtime behaviour: an
    agent that never migrated still runs. But `model: null` reads as "called
    no model", not "did not migrate", and an agent emitting `cost_micros`
    reached a green review board once on exactly that ambiguity. Decoding
    records the stale field so `agents check` can fail on it.
    """
    stale = CallResult.decode(
        _envelope(usage={"input_tokens": 9, "output_tokens": 3, "cost_micros": 4200}),
        capability="x",
    )
    assert stale.stale_usage == ("cost_micros",)
    assert stale.usage.model is None  # still decodes; still lenient


def test_a_migrated_agent_reports_no_stale_usage():
    fresh = CallResult.decode(
        _envelope(usage={"input_tokens": 9, "output_tokens": 3, "model": "claude-sonnet-5"}),
        capability="x",
    )
    assert fresh.stale_usage == ()


def test_check_fails_an_agent_still_emitting_a_removed_usage_field(stub, capsys):
    """The gate that would have caught a stale agent before review, not after.

    `usage.cost_micros` was replaced by `usage.model`. An agent that never
    migrated still decodes — deliberately, see
    `test_usage_reports_the_model_not_money` — but it then reports `model:
    null`, which claims it called no model. Every gate passed on exactly that
    branch once, so `check` has to be the thing that notices.
    """
    source = stub.workdir / "agent_main.py"
    source.write_text(
        source.read_text(encoding="utf-8").replace('"model": "test-model"', '"cost_micros": 4200'),
        encoding="utf-8",
    )

    assert main(["--root", str(stub.workdir.parent), "check"]) == 1
    err = capsys.readouterr().err
    assert "usage.cost_micros" in err
    assert "stub-agent" in err


def test_verify_and_check_agree_on_a_stale_usage_field(stub, capsys):
    """The two gates must not diverge — one used to have a check the other lacked.

    `agents check` gained the stale-usage check in #28 and `agents verify`'s copy
    of the same gate did not, so `verify` printed `ok  agents check` on a tree
    where `agents check` exited 1. A contributor sees green and CI goes red,
    which is the split test_verify.py exists to prevent.
    """
    source = stub.workdir / "agent_main.py"
    source.write_text(
        source.read_text(encoding="utf-8").replace('"model": "test-model"', '"cost_micros": 4200'),
        encoding="utf-8",
    )
    root = str(stub.workdir.parent)

    assert main(["--root", root, "check"]) == 1
    check_err = capsys.readouterr().err
    assert "usage.cost_micros" in check_err

    # Same tree, same gate, through verify. It must not report ok.
    assert main(["--root", root, "verify", "stub-agent"]) != 0
    verify_out = capsys.readouterr().out
    check_line = next(ln for ln in verify_out.splitlines() if "agents check" in ln)
    assert check_line.strip().startswith("FAIL"), f"verify disagreed with check: {check_line!r}"
