"""Loading and validating `agent.yaml`.

The manifest is the agent's own description of itself, living in the agent's
folder. The orchestrator reads it; nothing the orchestrator owns is read by
an agent. That direction is the whole point — an agent stays intact if this
package is deleted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from orchestrator.contract import DESCRIBE, PROTOCOL

MANIFEST_NAME = "agent.yaml"


class ManifestError(Exception):
    """An agent.yaml is missing, unreadable, or does not describe an agent."""


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AgentManifest:
    name: str
    description: str
    command: tuple[str, ...]
    workdir: Path
    capabilities: tuple[Capability, ...]

    def capability(self, name: str) -> Capability | None:
        return next((c for c in self.capabilities if c.name == name), None)

    @property
    def capability_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.capabilities)


def load(agent_dir: Path) -> AgentManifest:
    """Read and validate the manifest in `agent_dir`.

    Every failure raises ManifestError naming the file, because the caller is
    a person who has just added an agent and needs to know which one is wrong.
    """
    path = agent_dir / MANIFEST_NAME
    if not path.is_file():
        raise ManifestError(f"{path}: no {MANIFEST_NAME}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"{path}: must be a mapping")

    if raw.get("protocol") != PROTOCOL:
        raise ManifestError(f"{path}: protocol must be {PROTOCOL!r}, got {raw.get('protocol')!r}")

    name = _require_str(raw, "name", path)
    description = _require_str(raw, "description", path)

    runtime = raw.get("runtime")
    if not isinstance(runtime, dict):
        raise ManifestError(f"{path}: missing 'runtime' mapping")
    if runtime.get("type") != "subprocess":
        raise ManifestError(
            f"{path}: runtime.type must be 'subprocess' — it is the only transport "
            f"agentcall/v1 implements, got {runtime.get('type')!r}"
        )
    command = runtime.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
        raise ManifestError(f"{path}: runtime.command must be a non-empty list of strings")

    capabilities = _load_capabilities(raw.get("capabilities"), path)
    if not any(c.name == DESCRIBE for c in capabilities):
        # Cheap to declare, and it is the only capability the orchestrator can
        # call on an agent it knows nothing else about.
        raise ManifestError(f"{path}: every agent must declare the {DESCRIBE!r} capability")

    return AgentManifest(
        name=name,
        description=description,
        command=tuple(command),
        # Always the manifest's own directory. Not configurable: this is what
        # makes an agent's relative paths (.env, alembic.ini) resolve, and a
        # configurable cwd is a configurable way to get that silently wrong.
        workdir=agent_dir.resolve(),
        capabilities=capabilities,
    )


def _load_capabilities(raw: Any, path: Path) -> tuple[Capability, ...]:
    if not isinstance(raw, list) or not raw:
        raise ManifestError(f"{path}: 'capabilities' must be a non-empty list")
    seen: set[str] = set()
    out: list[Capability] = []
    for idx, item in enumerate(raw):
        where = f"{path}: capabilities[{idx}]"
        if not isinstance(item, dict):
            raise ManifestError(f"{where}: must be a mapping")
        cname = item.get("name")
        if not isinstance(cname, str) or not cname:
            raise ManifestError(f"{where}: missing 'name'")
        if cname in seen:
            raise ManifestError(f"{where}: duplicate capability {cname!r}")
        seen.add(cname)
        out.append(
            Capability(
                name=cname,
                description=str(item.get("description", "")),
                input_schema=_as_schema(item.get("input_schema")),
                output_schema=_as_schema(item.get("output_schema")),
            )
        )
    return tuple(out)


def _as_schema(raw: Any) -> dict[str, Any]:
    # Schemas are descriptive, not enforced here — the agent validates its own
    # input. We only insist they are objects so `describe` output stays typed.
    return raw if isinstance(raw, dict) else {}


def _require_str(raw: dict[str, Any], key: str, path: Path) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{path}: missing or empty {key!r}")
    return value.strip()
