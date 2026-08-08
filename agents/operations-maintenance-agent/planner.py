"""Maintenance planning: one Claude call, structured through a tool schema.

The capability's business logic lives here; `agentcall.py` is the adapter that
translates this module's exceptions into `agentcall/v1` error types. The three
exception types below are that translation's whole vocabulary — they exist so
the adapter can tell a caller's mistake apart from the model's, which a bare
`ValueError` cannot.
"""

from __future__ import annotations

import os
from typing import Any

from prompts import SYSTEM_PROMPT

# The repository's standard model, matching
# agents/realty-lead-gen/src/realty_lead_gen/config.py. A dated snapshot id
# pins the agent to a model that is eventually retired; the rolling alias does
# not.
#
# Changing this is not a one-line edit. `temperature` below is accepted on this
# model and is rejected outright — HTTP 400, not a warning — by Sonnet 5, Opus
# 4.7 and everything after them. Check `temperature` against the target
# model before bumping, or the first call after the bump fails.
MODEL = "claude-sonnet-4-5"

# Per-attempt ceiling and retry count are the SDK's own, rather than a
# hand-rolled backoff loop: `anthropic` already retries connection errors,
# 429s and 5xx with exponential backoff.
REQUEST_TIMEOUT_SECONDS = 60.0
MAX_RETRIES = 3

# 4096 matches realty-lead-gen's structured call. 1024 truncates a plan that
# carries four populated arrays, and truncation lands as a malformed-output
# failure rather than as anything a caller can act on.
MAX_TOKENS = 4096

# Ceiling on the rendered prompt, checked before anything is billed.
#
# NOTE: this number is a policy choice, not a derived constant — roughly 50k
# tokens at four characters each, which bounds the cost of one call while
# leaving headroom in any current context window. It is here because the
# alternative measured worse: with no ceiling, 20,000 work orders (8.8 MB)
# rendered into a single prompt and went to a paid call. Set it to whatever a
# shift actually looks like; do not remove it.
MAX_PROMPT_CHARS = 200_000

TOOL_NAME = "record_maintenance_plan"

# Mirrors `generate_maintenance_plan`'s output_schema in agent.yaml. The four
# arrays carry bare objects there, and they carry bare objects here — forcing
# the *envelope* to be well-formed does not require inventing a task's fields,
# and inventing them would put a shape in the contract that nobody agreed to.
PLAN_TOOL: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": "Record the optimized refinery maintenance plan.",
    "input_schema": {
        "type": "object",
        "required": [
            "plan_summary",
            "scheduled_tasks",
            "deferred_tasks",
            "risks",
            "recommendations",
        ],
        "properties": {
            "plan_summary": {
                "type": "string",
                "description": "High-level summary of the selected maintenance plan.",
            },
            "scheduled_tasks": {
                "type": "array",
                "description": "Maintenance tasks selected for execution.",
                "items": {"type": "object"},
            },
            "deferred_tasks": {
                "type": "array",
                "description": "Tasks postponed together with the reason.",
                "items": {"type": "object"},
            },
            "risks": {
                "type": "array",
                "description": "Operational risks considered during planning.",
                "items": {"type": "object"},
            },
            "recommendations": {
                "type": "array",
                "description": "Suggestions for improving maintenance efficiency.",
                "items": {"type": "object"},
            },
        },
    },
}

# Declared types, straight from agent.yaml's input_schema. Kept beside the
# manifest's wording on purpose: the orchestrator does not validate for us.
_REQUIRED_INPUT: tuple[tuple[str, type, str], ...] = (
    ("work_orders", list, "array"),
    ("equipment_status", list, "array"),
    ("technicians", list, "array"),
    ("production_constraints", dict, "object"),
)
_OPTIONAL_INPUT: tuple[tuple[str, type, str], ...] = (
    ("spare_parts", list, "array"),
    ("shift_details", dict, "object"),
)


class InvalidInputError(ValueError):
    """The caller's request was wrong. Becomes `invalid_request`."""


class MissingCredentialError(RuntimeError):
    """A needed credential is absent. Becomes `unavailable`."""


class ModelOutputError(RuntimeError):
    """The model's own output was unusable. Becomes `internal`, retryable.

    Carries the usage the call already incurred: the tokens were spent and
    billed before the output could be inspected, so dropping them here would
    under-report real cost on exactly the paths that waste it.
    """

    def __init__(self, message: str, usage: dict[str, int] | None = None) -> None:
        super().__init__(message)
        self.usage = usage or zero_usage()


def zero_usage() -> dict[str, object]:
    return {"input_tokens": 0, "output_tokens": 0, "model": None}


def usage_for(model: str, input_tokens: int, output_tokens: int) -> dict[str, object]:
    """Token counts plus the model that produced them.

    No money. A price is a vendor fact that changes without notice; this
    agent reports what it observed and lets cost be derived where it is
    actually needed. See docs/AGENT_PROTOCOL.md.
    """
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "model": model,
    }


def call_llm(user_prompt: str):
    """Call Claude with the plan tool forced, and return the raw response."""
    # Imported here, not at module scope: `describe` must answer on a machine
    # that cannot satisfy this agent's runtime.
    import anthropic  # noqa: PLC0415 — lazy import per rule 6, keeps describe cheap

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise MissingCredentialError("ANTHROPIC_API_KEY not configured")

    client = anthropic.Anthropic(
        api_key=api_key,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=MAX_RETRIES,
    )

    try:
        return _create(client, user_prompt)
    except anthropic.AuthenticationError as exc:
        # A rejected key is the same class of problem as an absent one — the
        # credential does not work and no retry will change that. The protocol
        # puts both under `unavailable`; letting this fall through to the
        # blanket handler would report a config error as `internal`.
        raise MissingCredentialError(f"ANTHROPIC_API_KEY was rejected: {exc}") from exc


def _create(client, user_prompt: str):
    return client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        # A structured call wants the same answer twice for the same input.
        temperature=0.0,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        tools=[PLAN_TOOL],
        tool_choice={"type": "tool", "name": TOOL_NAME},
    )


def _validate_input(payload: dict[str, Any]) -> None:
    for field, expected, declared in _REQUIRED_INPUT:
        if field not in payload:
            raise InvalidInputError(f"required field missing: {field}")
        if not isinstance(payload[field], expected):
            raise InvalidInputError(
                f"field {field!r} must be a JSON {declared}, "
                f"got {type(payload[field]).__name__}"
            )
    for field, expected, declared in _OPTIONAL_INPUT:
        if field in payload and not isinstance(payload[field], expected):
            raise InvalidInputError(
                f"field {field!r} must be a JSON {declared}, "
                f"got {type(payload[field]).__name__}"
            )


def _tool_payload(response) -> dict[str, Any] | None:
    """The forced tool's input, or None if the model did not emit it.

    Matched on the tool's name rather than the block's kind: a block from some
    other tool would otherwise be handed back as though it were a plan.
    """
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == TOOL_NAME:
            return block.input
    return None


def _validate_plan(plan: dict[str, Any], usage: dict[str, int]) -> None:
    """Check the plan against agent.yaml's output_schema — types, not just keys.

    `tool_choice` already constrains the model, so this should not fire. It is
    here because `ok: true` is a promise that the declared schema holds, and a
    presence-only check lets `risks` arrive as a string and still be called a
    success.
    """
    if not isinstance(plan.get("plan_summary"), str):
        raise ModelOutputError(
            f"model returned plan_summary as "
            f"{type(plan.get('plan_summary')).__name__}, expected string",
            usage,
        )
    for field in ("scheduled_tasks", "deferred_tasks", "risks", "recommendations"):
        if field not in plan:
            raise ModelOutputError(f"model omitted required field: {field}", usage)
        if not isinstance(plan[field], list):
            raise ModelOutputError(
                f"model returned {field} as {type(plan[field]).__name__}, expected array",
                usage,
            )


def generate_maintenance_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Turn operational constraints into an optimized plan."""
    _validate_input(payload)

    user_prompt = build_user_prompt(payload)
    if len(user_prompt) > MAX_PROMPT_CHARS:
        raise InvalidInputError(
            f"rendered prompt is {len(user_prompt)} characters, over the "
            f"{MAX_PROMPT_CHARS} limit; split the work orders across calls "
            f"and combine the plans"
        )

    response = call_llm(user_prompt)

    # Priced before the output is inspected, so a failure below still reports
    # what the call actually cost.
    usage = usage_for(MODEL, response.usage.input_tokens, response.usage.output_tokens)

    plan = _tool_payload(response)
    if plan is None:
        raise ModelOutputError("model did not emit a record_maintenance_plan tool call", usage)

    _validate_plan(plan, usage)
    return {"plan": plan, "usage": usage}


def build_user_prompt(payload: dict[str, Any]) -> str:
    """Render the operational constraints into one prompt.

    Takes the validated payload rather than six positional collections: the
    order of six same-typed arguments is impossible to get right at a glance,
    and `_validate_input` has already guaranteed the shapes read here.
    """
    work_orders = payload["work_orders"]
    equipment_status = payload["equipment_status"]
    technicians = payload["technicians"]
    production_constraints = payload["production_constraints"]
    spare_parts = payload.get("spare_parts", [])
    shift_details = payload.get("shift_details", {})

    return f"""
With the help of the following constraints, generate an optimized plan for refinery

work orders:
{work_orders}

equipment status:
{equipment_status}

technicians:
{technicians}

production constraints:
{production_constraints}

spare parts:
{spare_parts}

shift details:
{shift_details}

Clearly explain any deferred work and explain the trade offs.
"""
