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

from orchestrator.manifest import AgentManifest, ManifestError, load

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

    entries = raw.get("agents")
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
