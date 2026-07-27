"""Finding the agents.

Discovery is by declaration: `registry.yaml` lists paths, and each path must
contain an `agent.yaml`. Nothing globs directories — dropping a folder into
the repo, or leaving a scratch directory behind, must never be enough to
make something callable.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
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


#: The scaffold every agent starts as. Not an agent itself — see the leading
#: underscore convention in CLAUDE.md.
TEMPLATE_DIR = "_template"

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
        f"Add `- path: {name}` there, or the agent is never callable."
        for name in unregistered_agent_dirs(registry)
    ]

    template = _template_manifest(registry.root)
    readme = registry.root / "README.md"
    readme_text = readme.read_text(encoding="utf-8") if readme.is_file() else ""

    for manifest in registry:
        where = f"{manifest.name}/agent.yaml"

        problems.extend(
            f"{manifest.name}: no {required}. Every agent documents and "
            f"licenses itself — see CONTRIBUTING.md."
            for required in ("README.md", "LICENSE")
            if not (manifest.workdir / required).is_file()
        )

        if not manifest.test:
            problems.append(
                f"{where}: no runtime.test. Declare how this agent tests itself, or "
                f"nothing runs its tests — not CI, not a reviewer, not you."
            )

        if TODO_MARKER in (manifest.workdir / MANIFEST_NAME).read_text(encoding="utf-8"):
            problems.append(
                f"{where}: still has `{TODO_MARKER}` markers. Work through them, "
                f"then delete the comment."
            )

        if template is not None:
            if manifest.description.strip() == template.description.strip():
                problems.append(
                    f"{where}: description is still the template's. It is what a "
                    f"human — and later a router — picks this agent by."
                )
            if set(manifest.capability_names) == set(template.capability_names):
                problems.append(
                    f"{where}: offers only the template's example capabilities "
                    f"({', '.join(template.capability_names)}). Replace them with "
                    f"what this agent actually does."
                )

        if manifest.name not in readme_text:
            problems.append(
                f"{manifest.name}: missing from the README.md agents table. "
                f"The registry is machine-readable; the table is how people find it."
            )

    return problems


def _template_manifest(root: Path) -> AgentManifest | None:
    """The scaffold's manifest, or None if it has been removed."""
    try:
        return load(root / TEMPLATE_DIR)
    except ManifestError:
        return None


def unregistered_agent_dirs(registry: Registry) -> list[str]:
    """Folders that look like an agent but are not in the registry.

    Almost always a half-finished integration: someone copied `_template`,
    wrote their agent, and forgot step 4. Nothing else notices — discovery
    ignores unregistered folders by design — so the agent would merge and
    simply never be callable.

    A leading underscore means "not an agent" (`_template` is the working
    example nobody should be able to invoke), so those are skipped.
    """
    return sorted(
        path.name
        for path in registry.root.iterdir()
        if path.is_dir()
        and not path.name.startswith((".", "_"))
        and (path / "agent.yaml").is_file()
        and path.name not in registry.agents
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
