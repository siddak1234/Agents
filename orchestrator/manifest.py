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
class AgentEnv:
    """What an agent is allowed to see of the orchestrator's environment.

    Deny by default. An agent receives only the minimal base variables it
    needs to execute at all (see `runner.BASE_ENV`) plus what it names here.

    The alternative — handing every agent the whole environment — is one line
    shorter and means a trivial agent receives every credential the
    orchestrator happens to hold. An agent should not be able to read a
    database password because it was started by a process that had one.
    """

    #: Names or fnmatch patterns inherited from the orchestrator's environment.
    inherit: tuple[str, ...] = ()
    #: Literal values. Never put a secret here — this file is committed.
    set: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class AgentManifest:
    name: str
    description: str
    command: tuple[str, ...]
    workdir: Path
    capabilities: tuple[Capability, ...]
    env: AgentEnv = AgentEnv()
    #: How the agent tests itself, run from its own folder. Declared rather
    #: than discovered because test placement cannot be inferred without
    #: assuming a language, and an agent whose tests nothing can run is an
    #: agent whose tests nobody runs.
    test: tuple[str, ...] = ()

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

    test = runtime.get("test", [])
    if not isinstance(test, list) or not all(isinstance(c, str) for c in test):
        raise ManifestError(f"{path}: runtime.test must be a list of strings")

    capabilities = _load_capabilities(raw.get("capabilities"), path)
    if not any(c.name == DESCRIBE for c in capabilities):
        # Cheap to declare, and it is the only capability the orchestrator can
        # call on an agent it knows nothing else about.
        raise ManifestError(f"{path}: every agent must declare the {DESCRIBE!r} capability")

    return AgentManifest(
        name=name,
        description=description,
        command=tuple(command),
        env=_load_env(runtime.get("env"), path),
        test=tuple(test),
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


def _load_env(raw: Any, path: Path) -> AgentEnv:
    if raw is None:
        # Declaring nothing means inheriting nothing. Silence is the safe
        # answer, not the permissive one.
        return AgentEnv()
    if not isinstance(raw, dict):
        raise ManifestError(f"{path}: runtime.env must be a mapping")

    inherit = raw.get("inherit", [])
    if not isinstance(inherit, list) or not all(isinstance(v, str) and v for v in inherit):
        raise ManifestError(f"{path}: runtime.env.inherit must be a list of names or patterns")
    if "*" in inherit:
        # The whole point is that this is impossible to do by accident.
        raise ManifestError(
            f"{path}: runtime.env.inherit may not be '*'. Name the variables this agent "
            "actually needs; inheriting everything is what this setting exists to prevent."
        )

    assigned = raw.get("set", {})
    if not isinstance(assigned, dict) or not all(
        isinstance(k, str) and isinstance(v, (str, int, float, bool)) for k, v in assigned.items()
    ):
        raise ManifestError(f"{path}: runtime.env.set must be a mapping of names to scalars")

    return AgentEnv(
        inherit=tuple(inherit),
        set=tuple((k, str(v)) for k, v in assigned.items()),
    )


def _as_schema(raw: Any) -> dict[str, Any]:
    # Schemas are descriptive, not enforced here — the agent validates its own
    # input. We only insist they are objects so `describe` output stays typed.
    return raw if isinstance(raw, dict) else {}


def _require_str(raw: dict[str, Any], key: str, path: Path) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{path}: missing or empty {key!r}")
    return value.strip()
