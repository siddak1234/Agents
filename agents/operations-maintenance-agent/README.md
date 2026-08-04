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
    "input_tokens": 0,
    "output_tokens": 0,
    "cost_micros": 0
  },
  "error": null
}
```

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

```bash
python -m unittest discover -s tests -v
```

---

## Verifying the Agent

From the repository root:

```bash
uv run agents verify
```