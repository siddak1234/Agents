"""The agentcall/v1 wire types.

This module is the single definition of what crosses the process boundary.
It deliberately depends on nothing but the standard library: the contract
outlives whatever transport carries it, so it must not acquire a transport's
dependencies. See docs/AGENT_PROTOCOL.md for the prose.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Final

PROTOCOL: Final = "agentcall/v1"

#: The one capability every agent implements. Handshake, not business logic.
DESCRIBE: Final = "describe"

#: See docs/AGENT_PROTOCOL.md. `transport` is orchestrator-side only.
ERROR_TYPES: Final = frozenset(
    {"invalid_request", "unavailable", "timeout", "internal", "transport"}
)


class ProtocolError(Exception):
    """An agent returned something that is not a valid envelope."""


def _usage_count(raw: dict[str, Any], field: str) -> int:
    """One non-negative integer from a `usage` object.

    `int()` was too generous here. It accepts a bool — `True` became 1 token —
    and a numeric string, so an agent could report accounting in a type nobody
    intended and have it silently coerced. Counts are also never negative, and
    a negative one would quietly corrupt any total built on top of it.
    """
    value = raw.get(field, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(
            f"usage.{field} must be an integer, got {type(value).__name__} {value!r}"
        )
    if value < 0:
        raise ProtocolError(f"usage.{field} must not be negative, got {value}")
    return value


def _usage_model(raw: dict[str, Any]) -> str | None:
    """The model an agent called, or None when it called none.

    Absent is the same claim as `null` here: an agent that ran no model has
    no model to name. A non-string is a bug worth surfacing rather than
    coercing, for the same reason `_usage_count` refuses one.
    """
    value = raw.get("model")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProtocolError(
            f"usage.model must be a string or null, got {type(value).__name__} {value!r}"
        )
    return value


@dataclass(frozen=True, slots=True)
class Usage:
    """What an agent observed spending. Always present, zeroed when nothing was.

    Tokens and a model name, never money. See docs/AGENT_PROTOCOL.md: a price
    is a vendor fact that changes without notice, so it is derived where it is
    needed rather than copied into every agent.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    model: str | None = None

    @classmethod
    def from_wire(cls, raw: Any) -> Usage:
        """Decode `usage`, requiring it to be present.

        docs/AGENT_PROTOCOL.md says usage is always present, zeroed when nothing was
        spent — but this silently returned zeros for a missing or non-object
        `usage`, so "always present" was prose and nothing else. An agent could
        omit it entirely and its envelope still decoded as valid.

        Zeros an agent *declared* and zeros the orchestrator *invented* are
        different claims, and the whole point of mandatory accounting is that
        the second one is not available. Any aggregation built on top of this
        would otherwise rest on a field that was optional in practice.
        """
        if not isinstance(raw, dict):
            raise ProtocolError(
                f"usage must be an object, got {type(raw).__name__}. It is mandatory — "
                f"report zeros when nothing was spent."
            )
        return cls(
            input_tokens=_usage_count(raw, "input_tokens"),
            output_tokens=_usage_count(raw, "output_tokens"),
            model=_usage_model(raw),
        )

    def to_wire(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "model": self.model,
        }


@dataclass(frozen=True, slots=True)
class CallError:
    type: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        if self.type not in ERROR_TYPES:
            raise ProtocolError(f"unknown error type {self.type!r}")

    @classmethod
    def from_wire(cls, raw: Any) -> CallError:
        if not isinstance(raw, dict):
            raise ProtocolError("error must be an object")
        etype = raw.get("type")
        if etype not in ERROR_TYPES or etype == "transport":
            # An agent inventing its own taxonomy is a bug in the agent, but
            # losing the message would make it unfixable. Keep the text.
            #
            # `transport` gets the same treatment: docs/AGENT_PROTOCOL.md says
            # agents never emit it, yet because it sat in ERROR_TYPES an agent
            # could — and its envelope would be indistinguishable from an
            # orchestrator-side failure, which is the one distinction the
            # taxonomy exists to hold.
            return cls("internal", f"undeclared error type {etype!r}: {raw.get('message')}")
        return cls(str(etype), str(raw.get("message", "")), bool(raw.get("retryable", False)))

    def to_wire(self) -> dict[str, Any]:
        return {"type": self.type, "message": self.message, "retryable": self.retryable}


@dataclass(frozen=True, slots=True)
class CallRequest:
    capability: str
    input: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    deadline_ms: int | None = None

    def to_wire(self) -> dict[str, Any]:
        wire: dict[str, Any] = {
            "protocol": PROTOCOL,
            "capability": self.capability,
            "input": self.input,
            "request_id": self.request_id,
        }
        if self.deadline_ms is not None:
            wire["deadline_ms"] = self.deadline_ms
        return wire

    def encode(self) -> str:
        return json.dumps(self.to_wire())


@dataclass(frozen=True, slots=True)
class CallResult:
    ok: bool
    capability: str
    output: dict[str, Any] | None = None
    usage: Usage = field(default_factory=Usage)
    error: CallError | None = None

    @classmethod
    def failure(cls, capability: str, error: CallError) -> CallResult:
        return cls(ok=False, capability=capability, output=None, error=error)

    @classmethod
    def decode(cls, raw: str, *, capability: str) -> CallResult:
        """Parse an agent's stdout. Raises ProtocolError on anything invalid.

        Callers turn that into a `transport` error rather than propagating —
        a malformed envelope is the orchestrator's problem to report, not the
        caller's problem to interpret.
        """
        text = raw.strip()
        if not text:
            raise ProtocolError("agent produced no output on stdout")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            # Overwhelmingly this means the agent leaked a log line to
            # stdout. Say so — the generic parse error sends people hunting
            # in the wrong place.
            raise ProtocolError(
                f"stdout is not a single JSON object ({exc}); "
                "the agent most likely wrote a log line to stdout"
            ) from exc
        if not isinstance(payload, dict):
            raise ProtocolError("envelope must be a JSON object")
        if payload.get("protocol") != PROTOCOL:
            raise ProtocolError(f"expected protocol {PROTOCOL}, got {payload.get('protocol')!r}")

        ok = bool(payload.get("ok"))
        error = None if payload.get("error") is None else CallError.from_wire(payload["error"])
        if not ok and error is None:
            raise ProtocolError("ok=false requires an error object")
        output = payload.get("output")
        if ok and not isinstance(output, dict):
            raise ProtocolError("ok=true requires an output object")

        return cls(
            ok=ok,
            capability=str(payload.get("capability", capability)),
            output=output if ok else None,
            usage=Usage.from_wire(payload.get("usage")),
            error=error,
        )

    def to_wire(self) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL,
            "ok": self.ok,
            "capability": self.capability,
            "output": self.output,
            "usage": self.usage.to_wire(),
            "error": None if self.error is None else self.error.to_wire(),
        }
