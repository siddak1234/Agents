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

    #: Exact names, or a literal prefix plus one trailing `*`. See
    #: `_inherit_problem` for the rule and why it is shaped that way.
    inherit: tuple[str, ...] = ()
    #: Literal values. Never put a secret here — this file is committed.
    set: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        """Hold the inherit rule here as well as in the loader.

        `_load_env` already rejects a bad pattern, and with a better message —
        it knows which file the entry came from. This is the backstop: the
        invariant is what stops an agent reading a credential it never
        declared, and `build_env` consumes `inherit` far from where the loader
        validated it. Enforcing it on the type means no future construction
        path can skip it, which a check that lives only in the loader cannot
        promise.
        """
        for pattern in self.inherit:
            if problem := _inherit_problem(pattern):
                raise ManifestError(f"runtime.env.inherit entry {pattern!r} {problem}")


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
    #: How the agent checks its own source — lint, format, types. Same
    #: reasoning as `test`, and the same evidence: root tooling covers
    #: root-owned code only, so an agent's code was checked by nothing unless
    #: someone hand-wrote a pipeline for it. Exactly one agent had.
    lint: tuple[str, ...] = ()

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

    test = _load_command(runtime, "test", path)
    lint = _load_command(runtime, "lint", path)

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
        test=test,
        lint=lint,
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


#: Shortest literal prefix a wildcard entry may have. `A*` and `AB*` are not
#: names, they are sweeps that happen to be spelled short. Three characters is
#: where an entry starts describing a family instead.
#:
#: This threshold is not a safety guarantee and must not be read as one — see
#: `_inherit_problem` for what the rule does and does not buy.
MIN_INHERIT_PREFIX = 3


def _inherit_problem(pattern: str) -> str | None:
    """Why this entry may not be inherited, or None if it may.

    `build_env` matches these with `fnmatch`, which understands `?` and `[…]`
    as well as `*` — so rejecting the literal `"*"` alone left the door open.
    `?*`, `**`, `*_*`, and `[A-Z]*` each pass an equality check and then match
    essentially every variable the orchestrator holds. That is not a
    hypothetical: all four hand an agent the whole environment, credentials
    included, which is precisely what deny-by-default exists to prevent.

    Rather than enumerate the ways to write "everything", allow only the two
    shapes with an obvious meaning: an exact name, or a literal prefix with
    one trailing `*`.

    **What this does not do.** It rejects patterns that match approximately
    everything. It cannot tell whether a well-formed prefix is *too broad for
    this agent*: `AWS*` is three characters and legal here, and it reaches
    `AWS_SECRET_ACCESS_KEY`. So are `DB_*`, `API*` and `JWT*`. Whether a
    capability genuinely needs the family it names is a review question —
    CONTRIBUTING.md lists `runtime.env.inherit` broader than the capabilities
    justify as a blocking finding — and no character count decides it.
    """
    if any(char in pattern for char in "?[]"):
        return "may not use '?' or '[…]'. Use an exact name, or a prefix ending in '*'."

    stars = pattern.count("*")
    if stars == 0:
        return None
    if stars > 1 or not pattern.endswith("*"):
        return (
            "may use at most one '*', as the last character. "
            "Use an exact name, or a prefix ending in '*'."
        )
    if len(pattern) - 1 < MIN_INHERIT_PREFIX:
        return (
            f"needs at least {MIN_INHERIT_PREFIX} characters before the '*'. "
            f"A prefix this short sweeps in variables you did not mean to name."
        )
    return None


def _load_command(runtime: dict[str, Any], field: str, path: Path) -> tuple[str, ...]:
    """One optional `runtime.<field>` command list. Empty when absent."""
    value = runtime.get(field, [])
    if not isinstance(value, list) or not all(isinstance(c, str) for c in value):
        raise ManifestError(f"{path}: runtime.{field} must be a list of strings")
    return tuple(value)


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
    for pattern in inherit:
        if problem := _inherit_problem(pattern):
            raise ManifestError(f"{path}: runtime.env.inherit entry {pattern!r} {problem}")

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
