# Operations Maintenance Agent

An `agentcall/v1` agent that generates an optimized refinery maintenance plan
from operational constraints using LLM reasoning.

The agent accepts structured operational data, validates the request, builds a
maintenance planning prompt, and returns a structured JSON maintenance plan.

---

## Capability

### generate_maintenance_plan

Generates an optimized refinery maintenance plan while considering:

- Work orders
- Equipment status
- Technician availability
- Production constraints
- Spare parts inventory
- Shift details

The agent prioritizes safety, production continuity, technician utilization,
and equipment health.

---

## Input

Example request:

```json
{
  "protocol": "agentcall/v1",
  "capability": "generate_maintenance_plan",
  "input": {
    "work_orders": [],
    "equipment_status": [],
    "technicians": [],
    "production_constraints": {},
    "spare_parts": [],
    "shift_details": {}
  }
}
```

---

## Output

Example response:

```json
{
  "protocol": "agentcall/v1",
  "ok": true,
  "capability": "generate_maintenance_plan",
  "output": {
    "plan_summary": "...",
    "scheduled_tasks": [],
    "deferred_tasks": [],
    "risks": [],
    "recommendations": []
  },
  "usage": {
    "input_tokens": 842,
    "output_tokens": 213,
    "cost_micros": 5721
  },
  "error": null
}
```

The plan comes back through a forced tool call (`record_maintenance_plan`),
not by parsing JSON out of the model's prose, so a fenced or chatty reply
cannot turn a good plan into a failure. `cost_micros` is priced from the real
token counts — see `_MODEL_PRICING` in `planner.py`, which carries the same
rates as `realty-lead-gen`.

---

## Errors

| Type | When |
|---|---|
| `invalid_request` | The caller's input is missing a required field or has the wrong type. Nothing is sent to the model. |
| `unavailable` | `ANTHROPIC_API_KEY` is not set. The capability is disabled, not broken. |
| `internal` | The model did not call the tool, or returned a plan that violates the declared output schema. The call forces `tool_choice`, so this means the API did not keep its own contract. `retryable: false`, the value `docs/AGENT_PROTOCOL.md` gives this type. |

A failure that happened *after* the model call reports the tokens it spent
rather than zero: those tokens were billed whether or not the answer was
usable.

---

## Requirements

This agent requires an Anthropic API key.

Expose it through the environment variable:

```
ANTHROPIC_API_KEY
```

The repository runtime passes this variable to the agent through
`agent.yaml`.

---

## Running Tests

From this folder. These are the commands `agent.yaml` declares, which is what
`uv run agents test operations-maintenance-agent` and CI run:

```bash
uv run --frozen python -m unittest discover -s tests
uv run --frozen --extra dev ruff check .
```

The tests patch `planner.call_llm`, so they reach no network and need no key
even where a real one is exported.

---

## Verifying the Agent

From the repository root:

```bash
uv run agents verify
```
