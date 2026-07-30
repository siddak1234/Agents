"""Finding the agents.

Discovery is by declaration: `registry.yaml` lists paths, and each path must
contain an `agent.yaml`. Nothing globs directories — dropping a folder into
the repo, or leaving a scratch directory behind, must never be enough to
make something callable.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import yaml

from orchestrator.manifest import MANIFEST_NAME, AgentManifest, ManifestError, load

REGISTRY_NAME = "registry.yaml"
REGISTRY_VERSION = 2


class DiscoveryError(Exception):
    """The registry is unreadable, or disagrees with what is on disk."""


@dataclass(frozen=True, slots=True)
class Registry:
    root: Path
    agents: dict[str, AgentManifest]

    def get(self, name: str) -> AgentManifest:
        try:
            return self.agents[name]
        except KeyError:
            known = ", ".join(sorted(self.agents)) or "none"
            raise DiscoveryError(f"unknown agent {name!r}; registered: {known}") from None

    def __iter__(self) -> Iterator[AgentManifest]:
        return iter(sorted(self.agents.values(), key=lambda m: m.name))

    def __len__(self) -> int:
        return len(self.agents)


#: Where agents live. Tenant space: one folder per agent, nothing else. The
#: registry could in principle point anywhere — discovery is by declaration —
#: but this repository's layout rule is that it points here, and
#: `integration_problems` reports a registered agent that is not.
AGENTS_DIR = "agents"

#: The template's own name, as written inside its `agent.yaml` and
#: `agent_main.py`. Distinct from its path below: the scaffolder rewrites the
#: name and copies the path, and conflating the two renamed a new agent to
#: `agents/_template`.
TEMPLATE_NAME = "_template"

#: The scaffold every agent starts as, beside the real agents so `agents/` is
#: the one place to look. Not an agent itself: the leading underscore is what
#: keeps discovery from ever making it callable (see CLAUDE.md).
TEMPLATE_DIR = f"{AGENTS_DIR}/{TEMPLATE_NAME}"

#: Marks each spot the template expects a new agent to change.
TODO_MARKER = "TODO(new agent)"


def integration_problems(registry: Registry) -> list[str]:
    """Ways an agent can be *registered* without being *integrated*.

    Discovery only proves an agent loads and answers. That is a low bar: copy
    `_template`, change the name in two places, and everything passes while
    the agent still describes itself as a template and offers only the example
    capability. This is the check that tells the two apart.

    Each message names the file to edit, because the reader is someone
    contributing their first agent.
    """
    problems = [
        f"{name}/ holds an agent.yaml but is not in registry.yaml. "
        f"Add `- path: {AGENTS_DIR}/{name}` there (and put the folder under "
        f"{AGENTS_DIR}/ if it is not already), or the agent is never callable."
        for name in unregistered_agent_dirs(registry)
    ]

    template = _template_manifest(registry.root)
    readme = registry.root / "README.md"
    readme_text = readme.read_text(encoding="utf-8") if readme.is_file() else ""

    for manifest in registry:
        where = f"{manifest.workdir.name}/agent.yaml"

        # The layout rule, enforced where every other integration rule is. A
        # registered path outside agents/ loads fine — discovery is by
        # declaration — but it puts tenant code in platform space, and ten
        # interns following the previous agent's example is how a root fills
        # up. The registry says where an agent is; this says where it belongs.
        if manifest.workdir.parent != registry.root / AGENTS_DIR:
            problems.append(
                f"{manifest.name}: registered at "
                f"'{manifest.workdir.relative_to(registry.root)}', outside "
                f"{AGENTS_DIR}/. Agents live in {AGENTS_DIR}/<name> — move the "
                f"folder and update its registry.yaml entry."
            )

        problems.extend(
            f"{manifest.name}: no {required}. Every agent documents and "
            f"licenses itself — see docs/CONTRIBUTING.md."
            for required in ("README.md", "LICENSE")
            if not (manifest.workdir / required).is_file()
        )

        if not manifest.test:
            problems.append(
                f"{where}: no runtime.test. Declare how this agent tests itself, or "
                f"nothing runs its tests — not CI, not a reviewer, not you."
            )

        if not manifest.lint:
            problems.append(
                f"{where}: no runtime.lint. Root tooling checks root-owned code only, "
                f"so without this nothing lints or type-checks your source."
            )

        # The whole folder, not just the manifest. The template plants markers
        # in `agent_main.py`, `README.md` and its starter test as well, so a
        # manifest-only scan passed a copy with most of its TODOs untouched.
        if marked := _files_with_marker(manifest.workdir):
            problems.append(
                f"{manifest.name}: still has `{TODO_MARKER}` markers in "
                f"{', '.join(marked)}. Work through them, then delete the comment."
            )

        problems.extend(
            f"{manifest.name}: {unwanted} is not a file an agent may ship. "
            f"An agent is source, tests and its manifest — see "
            f"{TEMPLATE_DIR}/ for the whole of what one needs. If yours "
            f"genuinely requires this, say why in the pull request rather "
            f"than widening the list."
            for unwanted in _unallowed_files(manifest.workdir)
        )

        problems.extend(
            f"{where}: capability '{capability.name}' declares no {field}. "
            f"The schemas are how a caller learns to invoke you — an empty one "
            f"documents nothing."
            for capability in manifest.capabilities
            for field, schema in (
                ("input_schema", capability.input_schema),
                ("output_schema", capability.output_schema),
            )
            if not schema
        )

        if template is None:
            problems.append(
                f"{TEMPLATE_DIR}/ is missing or unreadable, so '{manifest.name}' "
                f"could not be compared against it. Two checks — description and "
                f"capabilities still being the template's — silently do not run "
                f"without it."
            )
        else:
            if _too_close_to(manifest.description, template.description):
                problems.append(
                    f"{where}: description is still the template's, or a light "
                    f"edit of it. It is what a human — and later a router — picks "
                    f"this agent by."
                )
            if set(manifest.capability_names) == set(template.capability_names):
                problems.append(
                    f"{where}: offers only the template's example capabilities "
                    f"({', '.join(template.capability_names)}). Replace them with "
                    f"what this agent actually does."
                )

        if not _has_readme_row(readme_text, manifest.name):
            problems.append(
                f"{manifest.name}: missing from the README.md agents table. "
                f"The registry is machine-readable; the table is how people find it."
            )

    return problems


#: Directories never worth scanning for markers: build output, caches, and
#: virtualenvs, any of which can hold thousands of files an agent did not write.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hypothesis",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)

#: Files above this are data, not source someone left a marker in.
MAX_SCANNED_BYTES = 1024 * 1024

#: How similar a description may be to the template's before it stops counting
#: as the contributor's own. Exact-match comparison was defeated by editing one
#: character, which is not the same as writing a description.
MAX_TEMPLATE_SIMILARITY = 0.8


#: What an agent folder may contain. An allowlist rather than a list of banned
#: things: the banned list is infinite and the allowed one is short, so the
#: allowlist is both smaller and closed — a file type nobody anticipated is
#: refused instead of admitted.
#:
#: It doubles as the mechanical half of the shape rule. `docker-compose.yml`,
#: `Dockerfile`, `alembic.ini`, `.env`, an archive or a stray `.sql` dump all
#: fail on their extension alone, so "an agent is not a service" stops being a
#: reviewer's opinion. Note `.yaml` is NOT allowed generally — only the two
#: yaml files an agent legitimately owns are named below, because admitting
#: arbitrary yaml is exactly how a compose file walks in.
ALLOWED_SUFFIXES = frozenset({".py", ".md", ".toml", ".json", ".lock", ".txt", ".example"})

ALLOWED_NAMES = frozenset(
    {
        MANIFEST_NAME,
        ".pre-commit-config.yaml",
        ".gitignore",
        ".python-version",
        "LICENSE",
        "Makefile",
    }
)

#: Directories that make an agent a service no matter what is inside them. The
#: allowlist alone cannot catch these: a migrations folder is full of ordinary
#: `.py` files and would sail through on extension.
DENIED_DIRS = frozenset({"alembic", "migrations"})


def _shippable_paths(workdir: Path) -> list[Path]:
    """What a commit from `workdir` would actually carry, relative to it.

    `rglob` answers "what is on disk", which is the wrong question. An agent
    folder legitimately holds a developer's own `.env` — `realty-lead-gen`'s
    README tells them to create one and its config reads it — and judging that
    as shipped failed `agents verify` on a file git is configured never to
    commit.

    `ls-files --cached --others --exclude-standard` is tracked files plus the
    untracked ones git would include. Deliberately not `check-ignore`, which
    reports a force-added `.env` as ignored and would let a real committed
    secret through; a tracked file is listed here however it got tracked.
    """
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],  # noqa: S607
            cwd=workdir,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:  # no git on this machine
        completed = None
    # Outside a repository git exits 128. A check that cannot see the index
    # should over-report rather than pass silently, so fall back to the tree.
    if completed is None or completed.returncode != 0:
        return [p.relative_to(workdir) for p in sorted(workdir.rglob("*"))]
    return sorted(Path(line) for line in completed.stdout.splitlines() if line)


def _unallowed_files(workdir: Path) -> list[str]:
    """Shippable paths under `workdir` an agent has no business shipping."""
    found = []
    for rel in _shippable_paths(workdir):
        parts = set(rel.parts)
        # Build output and caches are ignored, not rejected: they are not
        # committed, and failing someone's build because they ran pytest once
        # would be indefensible.
        if SKIP_DIRS & parts:
            continue
        if denied := DENIED_DIRS & parts:
            found.append(f"{sorted(denied)[0]}/")
            continue
        if not (workdir / rel).is_file():
            continue
        if rel.name in ALLOWED_NAMES or rel.suffix in ALLOWED_SUFFIXES:
            continue
        found.append(str(rel))
    return sorted(set(found))


def _files_with_marker(workdir: Path) -> list[str]:
    """Paths under `workdir`, relative, still carrying the TODO marker."""
    found = []
    for path in sorted(workdir.rglob("*")):
        if not path.is_file() or SKIP_DIRS & set(path.relative_to(workdir).parts):
            continue
        try:
            if path.stat().st_size > MAX_SCANNED_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # Binary, unreadable, or a broken symlink. Not our business, and
            # not a reason to fail the whole integration check.
            continue
        if TODO_MARKER in text:
            found.append(str(path.relative_to(workdir)))
    return found


def _normalized(text: str) -> str:
    return " ".join(text.lower().split())


def _too_close_to(description: str, template_description: str) -> bool:
    """Whether a description is the template's, allowing for a light edit."""
    if TODO_MARKER in description:
        return True
    mine, theirs = _normalized(description), _normalized(template_description)
    if mine == theirs:
        return True
    # Containment, checked before the ratio. `SequenceMatcher.ratio()` divides
    # by combined length, so padding dilutes it: the template sentence kept
    # verbatim and followed by two lines of unrelated prose scores 0.38 and
    # sails through — an easier evasion than the one-character edit the ratio
    # was added to catch. Keeping the sentence at all is the tell.
    if theirs in mine:
        return True
    return SequenceMatcher(None, mine, theirs).ratio() > MAX_TEMPLATE_SIMILARITY


def _has_readme_row(readme_text: str, name: str) -> bool:
    """Whether the agents table has a row for this agent.

    A bare substring search passed on any mention anywhere in the file —
    including a sentence promising to document the agent later. Narrowing that
    to table rows was not enough on its own: still matching a substring meant
    an agent called `gen` was satisfied by `realty-lead-gen`'s row, so any name
    contained in an existing agent's name would never need a row of its own.

    Match a whole cell instead, ignoring the markdown a row normally carries.
    """
    for line in readme_text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        for cell in line.split("|"):
            if _cell_names(cell) == name:
                return True
    return False


def _cell_names(cell: str) -> str:
    """A table cell reduced to the bare agent name it holds, if any.

    Rows are written `| [`parcel-geo`](./parcel-geo) | active | … |`, so the
    link, the backticks and the surrounding space all have to come off before
    the comparison means anything.
    """
    text = cell.strip()
    if match := re.fullmatch(r"\[(.*)\]\(.*\)", text):
        text = match.group(1).strip()
    return text.strip("`").strip()


def _template_manifest(root: Path) -> AgentManifest | None:
    """The scaffold's manifest, or None if it has been removed."""
    try:
        return load(root / TEMPLATE_DIR)
    except ManifestError:
        return None


def unregistered_agent_dirs(registry: Registry) -> list[str]:
    """Folders that look like an agent but are not in the registry.

    Almost always a half-finished integration: someone scaffolded their
    agent, wrote it, and forgot to register it. Nothing else notices —
    discovery ignores unregistered folders by design — so the agent would
    merge and simply never be callable.

    Both `agents/` and the repository root are scanned: the first is where
    agents belong, the second is where an old habit or a copied tutorial
    puts them. A stray at the root gets the same report and the layout
    check above tells the registered ones to move, so between the two every
    misplacement has a message.

    A leading underscore means "not an agent" (`_template` is the working
    example nobody should be able to invoke), so those are skipped.
    """
    candidates: list[Path] = []
    for parent in (registry.root / AGENTS_DIR, registry.root):
        if parent.is_dir():
            candidates.extend(parent.iterdir())
    registered_dirs = {manifest.workdir for manifest in registry}
    return sorted(
        path.name
        for path in candidates
        if path.is_dir()
        and not path.name.startswith((".", "_"))
        and (path / MANIFEST_NAME).is_file()
        and path.resolve() not in registered_dirs
    )


def repo_root(start: Path | None = None) -> Path:
    """Walk up from `start` to the directory holding registry.yaml."""
    here = (start or Path(__file__).resolve().parent).resolve()
    for candidate in (here, *here.parents):
        if (candidate / REGISTRY_NAME).is_file():
            return candidate
    raise DiscoveryError(f"no {REGISTRY_NAME} found at or above {here}")


def load_registry(root: Path | None = None) -> Registry:
    root = (root or repo_root()).resolve()
    path = root / REGISTRY_NAME

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DiscoveryError(f"{path}: cannot read: {exc}") from exc
    except yaml.YAMLError as exc:
        raise DiscoveryError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise DiscoveryError(f"{path}: must be a mapping")
    if raw.get("version") != REGISTRY_VERSION:
        raise DiscoveryError(
            f"{path}: expected version {REGISTRY_VERSION}, got {raw.get('version')!r}"
        )

    # `agents:` with nothing under it parses as None. A repository with no
    # agents yet is a legitimate state — it is what a fresh clone of this
    # scaffold looks like — so that reads as empty rather than malformed.
    entries = raw.get("agents") or []
    if not isinstance(entries, list):
        raise DiscoveryError(f"{path}: 'agents' must be a list")

    manifests: dict[str, AgentManifest] = {}
    for entry in entries:
        rel = entry.get("path") if isinstance(entry, dict) else entry
        if not isinstance(rel, str) or not rel:
            raise DiscoveryError(f"{path}: each agent entry needs a 'path'")

        agent_dir = root / rel
        if not agent_dir.is_dir():
            raise DiscoveryError(f"{path}: '{rel}' is registered but not a directory")

        try:
            manifest = load(agent_dir)
        except ManifestError as exc:
            raise DiscoveryError(str(exc)) from exc

        # The folder name is what a human types and what shows up in a path;
        # letting it drift from the declared name makes every error message
        # ambiguous about which agent is meant.
        if manifest.name != agent_dir.name:
            raise DiscoveryError(
                f"{agent_dir / 'agent.yaml'}: name {manifest.name!r} does not match "
                f"its folder {agent_dir.name!r}"
            )
        if manifest.name in manifests:
            raise DiscoveryError(f"{path}: duplicate agent {manifest.name!r}")
        manifests[manifest.name] = manifest

    return Registry(root=root, agents=manifests)
