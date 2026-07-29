"""Creating a new agent folder from the template.

The mechanical half of adding an agent — copying the scaffold, renaming it in
both the places that must agree, registering it, listing it in the README.
Every one of those is a step a contributor can forget, and forgetting any of
them produces an error message about something other than what went wrong.

The *judgement* half is deliberately not here. This leaves the description,
the capabilities, the `TODO(new agent)` markers and the missing `LICENSE`
exactly as the template has them, so `agents list --strict` still reports
them and working through that report is still the integration. A scaffolder
that filled those in would produce an agent that passes the gate while
meaning nothing — which is the failure `--strict` exists to catch.

This exists as a command rather than only as the `/new-agent` skill because a
contributor may not be using Claude Code at all. The skill interviews and then
calls this; without the skill you get the same folder and do the thinking
yourself.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import yaml

from orchestrator.discovery import AGENTS_DIR, REGISTRY_NAME, TEMPLATE_DIR, TEMPLATE_NAME

#: Lowercase, digits and single hyphens. This becomes a folder name, a YAML
#: key, a CLI argument and a URL path segment; anything else is a portability
#: problem somewhere downstream.
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

#: Root names that belong to the repository rather than to an agent. Taking
#: one would shadow tooling in ways that fail confusingly and late.
RESERVED = frozenset(
    {
        "docs",
        "orchestrator",
        "registry",
        "scripts",
        "shared",
        "tests",
        "tools",
    }
)


class ScaffoldError(Exception):
    """The agent could not be created. The message says what to do instead."""


def create_agent(root: Path, name: str) -> list[str]:
    """Create `root/agents/name` from the template. Returns what was done.

    Raises `ScaffoldError` before touching anything if the name is unusable,
    so a rejected name never leaves a half-made folder behind.
    """
    _validate(root, name)
    _check_registry_shape(root)

    template = root / TEMPLATE_DIR
    if not template.is_dir():
        raise ScaffoldError(f"no {TEMPLATE_DIR}/ to copy from at {root}")

    target = root / AGENTS_DIR / name
    target.parent.mkdir(exist_ok=True)
    # `__pycache__` from someone running the template locally would otherwise
    # be the new agent's first committed file.
    shutil.copytree(template, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    done = [f"created {AGENTS_DIR}/{name}/ from {TEMPLATE_DIR}/"]
    done.append(_rename(target, name))
    done.append(_register(root, name))
    done.append(_add_readme_row(root, name))
    return done


def _validate(root: Path, name: str) -> None:
    if not NAME_PATTERN.match(name):
        raise ScaffoldError(
            f"{name!r} is not a usable agent name. Use lowercase words joined by "
            f"single hyphens, starting with a letter — for example 'parcel-geo'."
        )
    if name in RESERVED:
        raise ScaffoldError(f"{name!r} is reserved for repository tooling. Pick another name.")
    if name.startswith("_"):
        raise ScaffoldError(f"a leading underscore means 'not an agent'; {name!r} would never load")
    if (root / AGENTS_DIR / name).exists():
        raise ScaffoldError(
            f"{AGENTS_DIR}/{name}/ already exists. Pick another name, or delete it first."
        )


def _rename(target: Path, name: str) -> str:
    """The two declarations of the name that discovery requires to agree.

    `agent.yaml`'s `name:` must equal the folder name or the registry refuses
    to load it, and `AGENT_NAME` is what `describe` reports back — a mismatch
    there passes discovery and fails the handshake instead, one step later and
    much less obviously.
    """
    manifest = target / "agent.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(f"name: {TEMPLATE_NAME}", f"name: {name}", 1),
        encoding="utf-8",
    )

    entry = target / "agent_main.py"
    entry.write_text(
        entry.read_text(encoding="utf-8").replace(
            f'AGENT_NAME = "{TEMPLATE_NAME}"', f'AGENT_NAME = "{name}"', 1
        ),
        encoding="utf-8",
    )
    return f"set name to {name!r} in agent.yaml and agent_main.py"


def _check_registry_shape(root: Path) -> None:
    """Refuse to scaffold against a registry the textual append would corrupt.

    `_register` appends `  - path: agents/<name>` to the end of the file, which is
    only correct when `agents:` is the last top-level key with its entries (or
    nothing) beneath it. A registry with no `agents:` key at all used to get
    the entry appended anyway — the file then parsed as a bare list, every
    later command failed on it, and the scaffolder had already reported
    "registered" as a success step. Checked before anything is created, so a
    bad registry never leaves a half-made folder behind.
    """
    path = root / REGISTRY_NAME
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ScaffoldError(f"{path}: cannot read: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ScaffoldError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict) or "agents" not in raw:
        raise ScaffoldError(
            f"{path}: no top-level `agents:` key to register under. "
            f"Add `agents:` (an empty list is fine), then rerun."
        )


def _register(root: Path, name: str) -> str:
    """Append to registry.yaml textually, preserving its comments.

    Round-tripping through PyYAML would reformat the file and drop every
    comment in it, which is a large diff and a real loss for a file whose
    comments explain the discovery-by-declaration rule.

    Textual appends are easy to get subtly wrong, so the candidate text is
    parsed before anything is written: if the new entry would not land inside
    `agents:`, the file is left untouched and the step fails instead of
    "succeeding" with a registry nothing can load.
    """
    path = root / REGISTRY_NAME
    text = path.read_text(encoding="utf-8")
    if re.search(rf"^\s*-\s*path:\s*(?:{AGENTS_DIR}/)?{re.escape(name)}\s*$", text, re.M):
        return f"{REGISTRY_NAME} already lists {name}"

    appended = f"{text.rstrip()}\n  - path: {AGENTS_DIR}/{name}\n"
    if not _registers_cleanly(appended, name):
        raise ScaffoldError(
            f"{path}: appending `- path: {AGENTS_DIR}/{name}` would not land under `agents:` "
            f"— is `agents:` the last key in the file? Add the entry by hand."
        )
    path.write_text(appended, encoding="utf-8")
    return f"registered {name} in {REGISTRY_NAME}"


def _registers_cleanly(text: str, name: str) -> bool:
    """Whether `text` parses and lists `name` under `agents:`."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError:
        return False
    if not isinstance(raw, dict):
        return False
    entries = raw.get("agents") or []
    if not isinstance(entries, list):
        return False
    return any(
        (entry.get("path") if isinstance(entry, dict) else entry) == f"{AGENTS_DIR}/{name}"
        for entry in entries
    )


def _add_readme_row(root: Path, name: str) -> str:
    """Add a row to the agents table, directly after the last existing one.

    Located by the table's own shape rather than by a marker comment, so the
    README needs nothing added to it to keep working.
    """
    path = root / "README.md"
    if not path.is_file():
        return "no README.md to update"

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    row = f"| [`{name}`](./{AGENTS_DIR}/{name}) | active | `TODO` |\n"

    # Find the header separator (`|---|---|`), then take every row under it
    # until the table ends. Anchoring on the separator rather than on a
    # marker comment means the README needs nothing added to stay scaffoldable.
    last = None
    for i, line in enumerate(lines):
        if last is None:
            if re.match(r"^\|[-|\s:]+\|\s*$", line):
                last = i
        elif line.startswith("|"):
            last = i
        else:
            break

    if last is None:
        return "README.md has no agents table; add a row by hand"

    lines.insert(last + 1, row)
    path.write_text("".join(lines), encoding="utf-8")
    return f"added {name} to the README agents table"
