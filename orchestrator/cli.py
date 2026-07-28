"""`agents` — the command-line face of the orchestrator.

    agents list [--strict] [--json]
    agents describe <agent> [--static]
    agents call <agent> <capability> [--input JSON | --input-file PATH]
    agents check [agents…]
    agents test [agents…]
    agents lint [agents…]
    agents new <name>
    agents verify [agents…]

`verify` is the contributor's command: every deterministic gate CI runs,
reported at once. (The review board in CI is model-driven and not here.)
`check` is the cheap heart of it — it loads every manifest and calls
`describe` on every agent, which catches a registry that has drifted from
disk, a broken entrypoint, or a manifest that no longer matches its code.
"""

from __future__ import annotations

import argparse
import functools
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from orchestrator.contract import DESCRIBE, CallRequest, CallResult
from orchestrator.discovery import (
    DiscoveryError,
    Registry,
    integration_problems,
    load_registry,
)
from orchestrator.manifest import AgentManifest
from orchestrator.runner import build_env, call, describe
from orchestrator.scaffold import ScaffoldError, create_agent


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2

    try:
        registry = load_registry(args.root)
    except DiscoveryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    handlers = {
        "list": _cmd_list,
        "describe": _cmd_describe,
        "call": _cmd_call,
        "check": _cmd_check,
        "test": _cmd_test,
        "lint": _cmd_lint,
        "new": _cmd_new,
        "verify": _cmd_verify,
    }
    return handlers[args.command](registry, args)


def _cmd_new(registry: Registry, args: argparse.Namespace) -> int:
    """Scaffold an agent folder, registered but deliberately not finished."""
    try:
        done = create_agent(registry.root, args.name)
    except ScaffoldError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for step in done:
        print(f"  {step}")
    print(
        f"\nNext, in {args.name}/:\n"
        f"  1. agent.yaml — write the description and your real capabilities,\n"
        f"     each with an input and an output schema. Point runtime.lint at\n"
        f"     real linting once you have dependencies; nothing else checks\n"
        f"     your code.\n"
        f"  2. agent_main.py — implement them. Keep `describe`.\n"
        f"  3. Add a LICENSE. It is deliberately not copied: licensing is per\n"
        f"     agent and choosing one should be a decision, not an inheritance.\n"
        f"  4. Replace the README and the tests with your own.\n"
        f"  5. Clear every `TODO(new agent)` marker. They are in all four\n"
        f"     files, not just the manifest, and the whole folder is checked.\n\n"
        f"Then run `agents verify`. It will still fail, and what it reports is\n"
        f"the list of things that make this an agent rather than a copy."
    )
    return 0


def _cmd_list(registry: Registry, args: argparse.Namespace) -> int:
    if args.strict:
        problems = integration_problems(registry)
        if problems:
            for problem in problems:
                print(f"error: {problem}", file=sys.stderr)
            print(
                f"\n{len(problems)} problem(s). An agent that loads is not yet an "
                f"agent that is integrated.",
                file=sys.stderr,
            )
            return 1

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "name": m.name,
                        "description": m.description,
                        "capabilities": list(m.capability_names),
                    }
                    for m in registry
                ],
                indent=2,
            )
        )
        return 0
    if not len(registry):
        print("no agents registered")
        return 0
    width = max(len(m.name) for m in registry)
    for m in registry:
        caps = ", ".join(c for c in m.capability_names if c != DESCRIBE) or "—"
        print(f"{m.name:<{width}}  {m.description}")
        print(f"{'':<{width}}  capabilities: {caps}")
    return 0


def _cmd_describe(registry: Registry, args: argparse.Namespace) -> int:
    try:
        manifest = registry.get(args.agent)
    except DiscoveryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.static:
        # Read the manifest without running anything. Useful when the agent's
        # dependencies are not installed on this machine.
        print(
            json.dumps(
                {
                    "name": manifest.name,
                    "description": manifest.description,
                    "workdir": str(manifest.workdir),
                    "command": list(manifest.command),
                    "capabilities": [
                        {
                            "name": c.name,
                            "description": c.description,
                            "input_schema": c.input_schema,
                            "output_schema": c.output_schema,
                        }
                        for c in manifest.capabilities
                    ],
                },
                indent=2,
            )
        )
        return 0

    return _emit(describe(manifest))


def _cmd_call(registry: Registry, args: argparse.Namespace) -> int:
    try:
        manifest = registry.get(args.agent)
    except DiscoveryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if manifest.capability(args.capability) is None:
        known = ", ".join(manifest.capability_names)
        print(
            f"error: agent {manifest.name!r} declares no capability "
            f"{args.capability!r}; declared: {known}",
            file=sys.stderr,
        )
        return 2

    try:
        payload = _read_input(args)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read input: {exc}", file=sys.stderr)
        return 2

    result = call(
        manifest,
        CallRequest(
            capability=args.capability,
            input=payload,
            request_id=args.request_id or "",
            deadline_ms=args.deadline_ms,
        ),
        timeout_s=args.timeout,
    )
    return _emit(result)


def _cmd_check(registry: Registry, args: argparse.Namespace) -> int:
    """Describe the named agents, or all of them when none are named.

    CI names the agents a change actually touched. Checking every agent on
    every pull request would build every agent's environment — so adding one
    agent would slow down and could fail a pull request that has nothing to do
    with it, and the author would be looking at someone else's breakage.
    The full sweep is still right when shared code changes; that is the
    caller's judgement to make, not this command's.
    """
    if not len(registry):
        print("no agents registered")
        return 0

    if args.agents:
        try:
            selected = [registry.get(name) for name in args.agents]
        except DiscoveryError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    else:
        selected = list(registry)

    failed = 0
    for manifest in selected:
        result = describe(manifest)
        if not result.ok:
            failed += 1
            detail = result.error.message if result.error else "unknown failure"
            print(f"FAIL  {manifest.name}\n      {detail}", file=sys.stderr)
            continue

        # The agent ran — but does it offer what it advertises? Printing the
        # manifest's list here would restate the file we just read and prove
        # nothing. Comparing it against what the agent reported is the whole
        # point of a handshake: a capability declared and not implemented is a
        # caller's runtime error, and one implemented and not declared is
        # outside the contract.
        drift = _describe_drift(manifest, result.output)
        if drift:
            failed += 1
            print(f"FAIL  {manifest.name}\n      {drift}", file=sys.stderr)
            continue

        print(f"ok    {manifest.name}  ({', '.join(manifest.capability_names)})")
    return 1 if failed else 0


def _cmd_test(registry: Registry, args: argparse.Namespace) -> int:
    """Run each agent's own test command, in its own folder.

    The orchestrator does not know how to test an agent and should not guess —
    it runs what the manifest declares. Without this nothing runs a
    contributed agent's tests: the root suite tests the orchestrator against a
    stub, and CI only knows about pipelines that already exist.
    """
    try:
        selected = [registry.get(n) for n in args.agents] if args.agents else list(registry)
    except DiscoveryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    return _run_declared(selected, "test")


def _cmd_lint(registry: Registry, args: argparse.Namespace) -> int:
    """Run each agent's own lint command, in its own folder.

    Root tooling covers root-owned code only, by design — so until this
    existed, a contributed agent's source was checked by nothing unless
    someone hand-wrote a pipeline for it. Exactly one agent had.
    """
    try:
        selected = [registry.get(n) for n in args.agents] if args.agents else list(registry)
    except DiscoveryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    return _run_declared(selected, "lint")


def _declared(manifest: AgentManifest, field: str) -> tuple[str, ...]:
    value = getattr(manifest, field)
    assert isinstance(value, tuple)  # noqa: S101 — narrowing a known field
    return value


def _run_declared(selected: list[AgentManifest], field: str) -> int:
    """Run each agent's declared `runtime.<field>` command, from its folder.

    One implementation for `test` and `lint`. They differ only in which field
    they read: same working directory, same environment policy, same
    reporting — and a second copy would be a second place for the environment
    handling below to drift.
    """
    failed = 0
    for manifest in selected:
        command = _declared(manifest, field)
        if not command:
            failed += 1
            print(f"FAIL  {manifest.name}: declares no runtime.{field}", file=sys.stderr)
            continue
        print(f"---- {manifest.name}: {' '.join(command)}", flush=True)
        completed = subprocess.run(  # noqa: S603 — command comes from a repo-controlled manifest
            list(command),
            cwd=manifest.workdir,
            # The same allow-list a call gets. This is arbitrary code from a
            # contributor, run on a maintainer's machine and in CI where the
            # real secrets live, so it is the last place to widen the
            # environment. It also stops the orchestrator's own VIRTUAL_ENV
            # bleeding through and pointing the agent's tooling at the wrong
            # interpreter.
            env=build_env(manifest),
            check=False,
        )
        if completed.returncode != 0:
            failed += 1
            print(f"FAIL  {manifest.name}: exit {completed.returncode}", file=sys.stderr)
        else:
            print(f"ok    {manifest.name}")
    return 1 if failed else 0


#: What `verify` runs, in the order CI runs it. Each entry is a label and the
#: arguments after `python -m`. Invoked through `sys.executable` rather than a
#: bare command name so it works without `uv` on PATH and cannot pick up a
#: different interpreter's tools by accident.
#:
#: The secret scan is here because "verify runs every deterministic gate CI
#: runs" is a promise this list has already broken once: gitleaks was added
#: to CI and not here, and for that window a branch could print "all gates
#: pass" while carrying a committed credential that CI would reject.
_TOOL_STEPS = (
    ("secret scan", ("pre_commit", "run", "gitleaks", "--all-files")),
    ("ruff format", ("ruff", "format", "--check", "orchestrator", "agents/_template")),
    ("ruff check", ("ruff", "check", "orchestrator", "agents/_template")),
    ("mypy", ("mypy", "orchestrator")),
    ("pytest", ("pytest", "-q")),
)


def _cmd_verify(registry: Registry, args: argparse.Namespace) -> int:
    """Run every deterministic gate CI runs; exit non-zero if any failed.

    Deliberately does not stop at the first failure. A contributor working
    through their first agent wants the whole list — often one root cause
    shows up as three of these — and a single block of output is something
    they can paste somewhere and ask about.

    The one thing checked *before* any gate runs is the scope itself: a
    misspelled agent name must be an error up front, not eight vacuous passes.
    """
    try:
        _verify_scope(registry, args)
    except DiscoveryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    results: list[tuple[str, str, str]] = []

    for label, argv in _TOOL_STEPS:
        # pre-commit is configuration-driven: with no config at this root
        # there is nothing to run, which is a SKIP, not a FAIL — a scaffolded
        # repository without hooks should not be told its secrets leaked.
        if argv[0] == "pre_commit" and not (registry.root / ".pre-commit-config.yaml").is_file():
            results.append((label, "SKIP", "no .pre-commit-config.yaml at this root"))
            continue
        completed = subprocess.run(  # noqa: S603 — fixed argv, no user input
            [sys.executable, "-m", *argv],
            cwd=registry.root,
            capture_output=True,
            text=True,
            check=False,
        )
        # A tool that is not installed is not a failing gate — it is a gate
        # that did not run, and saying "FAIL" for it would send someone
        # looking for a defect in their agent.
        if completed.returncode != 0 and f"No module named {argv[0]}" in completed.stderr:
            results.append((label, "SKIP", f"{argv[0]} is not installed here"))
            continue
        output = (completed.stdout + completed.stderr).strip()
        results.append((label, "ok" if completed.returncode == 0 else "FAIL", output))

    for label, handler in (
        ("agents list --strict", _verify_strict),
        ("agents check", _verify_check),
        ("agents lint", functools.partial(_verify_declared, field="lint")),
        ("agents test", functools.partial(_verify_declared, field="test")),
    ):
        results.append((label, *handler(registry, args)))

    failed = [r for r in results if r[1] == "FAIL"]
    for label, status, output in results:
        print(f"{status:<4}  {label}")
        if status == "FAIL" and output:
            print("\n".join(f"        {line}" for line in output.splitlines()[:40]))

    if failed:
        print(
            f"\n{len(failed)} of {len(results)} gates failed. These are the same "
            f"checks CI runs, so fixing them here is fixing the build.",
            file=sys.stderr,
        )
        return 1
    print(f"\nAll {len(results)} gates pass.")
    return 0


def _verify_strict(registry: Registry, args: argparse.Namespace) -> tuple[str, str]:
    problems = integration_problems(registry)
    return ("FAIL", "\n".join(problems)) if problems else ("ok", "")


def _verify_check(registry: Registry, args: argparse.Namespace) -> tuple[str, str]:
    failures = []
    for manifest in _verify_scope(registry, args):
        result = describe(manifest)
        if not result.ok:
            failures.append(f"{manifest.name}: {result.error.message if result.error else '?'}")
        elif drift := _describe_drift(manifest, result.output):
            failures.append(f"{manifest.name}: {drift}")
    return ("FAIL", "\n".join(failures)) if failures else ("ok", "")


def _verify_declared(registry: Registry, args: argparse.Namespace, field: str) -> tuple[str, str]:
    """`_run_declared`, quietly, keeping only the tail of a failure."""
    failures = []
    for manifest in _verify_scope(registry, args):
        command = _declared(manifest, field)
        if not command:
            failures.append(f"{manifest.name}: declares no runtime.{field}")
            continue
        completed = subprocess.run(  # noqa: S603 — command comes from a repo-controlled manifest
            list(command),
            cwd=manifest.workdir,
            env=build_env(manifest),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            tail = (completed.stdout + completed.stderr).strip().splitlines()[-20:]
            failures.append(f"{manifest.name}: exit {completed.returncode}\n" + "\n".join(tail))
    return ("FAIL", "\n".join(failures)) if failures else ("ok", "")


def _verify_scope(registry: Registry, args: argparse.Namespace) -> list[AgentManifest]:
    """Named agents, or all of them. Raises `DiscoveryError` on an unknown name.

    This used to drop unknown names silently, which made every agent-scoped
    gate pass vacuously: `agents verify tpyo` printed "All 8 gates pass" and
    exited 0 while verifying nothing. A scope that cannot be resolved is an
    error, not an empty scope.
    """
    if not args.agents:
        return list(registry)
    return [registry.get(n) for n in args.agents]


def _describe_drift(manifest: AgentManifest, output: dict[str, Any] | None) -> str | None:
    """Compare `describe`'s answer with the manifest. None when they agree."""
    reported_name = (output or {}).get("name")
    parts = []

    # The name is declared twice — `agent.yaml` and the entrypoint's own
    # constant — and discovery only checks the manifest against its folder.
    # Nothing noticed the third copy drifting, so an agent could answer under a
    # different name than the one it is registered and called by.
    if reported_name != manifest.name:
        parts.append(
            f"describe reports name {reported_name!r} but the manifest and folder "
            f"say {manifest.name!r}; the name is declared in both agent.yaml and "
            f"the entrypoint and they have drifted"
        )

    raw = (output or {}).get("capabilities")
    if not isinstance(raw, list):
        parts.append(
            "describe did not report a capabilities list, so the manifest cannot be verified"
        )
        return "; ".join(parts)

    reported = {c["name"] for c in raw if isinstance(c, dict) and isinstance(c.get("name"), str)}
    declared = set(manifest.capability_names)
    if missing := sorted(declared - reported):
        parts.append(f"declared in agent.yaml but not implemented: {', '.join(missing)}")
    if extra := sorted(reported - declared):
        parts.append(f"implemented but not declared in agent.yaml: {', '.join(extra)}")
    return "; ".join(parts) or None


def _read_input(args: argparse.Namespace) -> dict[str, Any]:
    # Reading stdin implicitly when no flag is given looks convenient and
    # hangs forever the moment this runs anywhere without a terminal — a
    # script, a cron job, CI. Stdin is only ever read when asked for by name.
    if args.input_file == "-":
        raw = sys.stdin.read()
    elif args.input_file:
        raw = Path(args.input_file).read_text(encoding="utf-8")
    elif args.input:
        raw = args.input
    else:
        raw = "{}"
    payload = json.loads(raw or "{}")
    if not isinstance(payload, dict):
        raise json.JSONDecodeError("input must be a JSON object", raw, 0)
    return payload


def _emit(result: CallResult) -> int:
    print(json.dumps(result.to_wire(), indent=2))
    return 0 if result.ok else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agents", description="Call the agents in this repo.")
    parser.add_argument("--root", type=Path, default=None, help="repo root (default: auto-detect)")
    sub = parser.add_subparsers(dest="command")

    p_list = sub.add_parser("list", help="list registered agents")
    p_list.add_argument("--json", action="store_true")
    p_list.add_argument(
        "--strict",
        action="store_true",
        help="also fail if a folder holds an agent.yaml but is not registered",
    )

    p_desc = sub.add_parser("describe", help="describe one agent")
    p_desc.add_argument("agent")
    p_desc.add_argument(
        "--static",
        action="store_true",
        help="read the manifest without running the agent",
    )

    p_call = sub.add_parser("call", help="call a capability")
    p_call.add_argument("agent")
    p_call.add_argument("capability")
    p_call.add_argument("--input", help="inline JSON object (default: {})")
    p_call.add_argument("--input-file", help="path to a JSON object, or '-' for stdin")
    p_call.add_argument("--request-id", default="")
    p_call.add_argument("--deadline-ms", type=int, default=None)
    p_call.add_argument("--timeout", type=float, default=None, help="hard kill after N seconds")

    p_check = sub.add_parser(
        "check", help="describe the named agents (default: all); non-zero if any fails"
    )
    p_check.add_argument(
        "agents",
        nargs="*",
        help="agent names to check. Omit to check every registered agent.",
    )

    p_test = sub.add_parser(
        "test", help="run each agent's own declared test command, in its own folder"
    )
    p_test.add_argument(
        "agents", nargs="*", help="agent names to test. Omit to test every registered agent."
    )

    p_lint = sub.add_parser(
        "lint", help="run each agent's own declared lint command, in its own folder"
    )
    p_lint.add_argument(
        "agents", nargs="*", help="agent names to lint. Omit to lint every registered agent."
    )

    p_new = sub.add_parser("new", help="scaffold a new agent folder from the template")
    p_new.add_argument("name", help="folder and agent name, e.g. parcel-geo")

    p_verify = sub.add_parser(
        "verify", help="run every deterministic gate CI runs and report all at once"
    )
    p_verify.add_argument(
        "agents",
        nargs="*",
        help="agent names to check and test. Omit to cover every registered agent.",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
