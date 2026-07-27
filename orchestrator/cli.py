"""`agents` — the command-line face of the orchestrator.

    agents list
    agents describe <agent>
    agents call <agent> <capability> [--input JSON | --input-file PATH]
    agents check

`check` is the one to run in CI: it loads every manifest and calls
`describe` on every agent, which catches a registry that has drifted from
disk, a broken entrypoint, or a manifest that no longer matches its code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from orchestrator.contract import DESCRIBE, CallRequest, CallResult
from orchestrator.discovery import (
    DiscoveryError,
    Registry,
    load_registry,
    unregistered_agent_dirs,
)
from orchestrator.runner import call, describe


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
    }
    return handlers[args.command](registry, args)


def _cmd_list(registry: Registry, args: argparse.Namespace) -> int:
    if args.strict:
        orphans = unregistered_agent_dirs(registry)
        if orphans:
            for name in orphans:
                print(
                    f"error: {name}/ contains an agent.yaml but is not in registry.yaml.\n"
                    f"       Add `- path: {name}` to registry.yaml, or the agent is "
                    f"never callable.",
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
        if result.ok:
            caps = ", ".join(manifest.capability_names)
            print(f"ok    {manifest.name}  ({caps})")
        else:
            failed += 1
            detail = result.error.message if result.error else "unknown failure"
            print(f"FAIL  {manifest.name}\n      {detail}", file=sys.stderr)
    return 1 if failed else 0


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
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
