import json

from typing import Any

from prompts import SYSTEM_PROMPT

import os

from anthropic import Anthropic

def generate_maintenance_plan(payload:dict[str,Any]) -> dict[str,Any]:
    """Takes in the operational constraints and generates optimized plan to maximize production."""

    api_key = os.getenv("ANTHROPIC_API_KEY")

    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    client = Anthropic(api_key=api_key)

    required_fields = [
        "work_orders" ,
        "equipment_status",
        "production_constraints" ,
        "technicians" ,
    ]

    for field in required_fields:

        if field not in payload:

            raise ValueError(f"Required field missing : {field}")

    work_orders = payload["work_orders"]
    equipment_status = payload["equipment_status"]
    production_constraints = payload["production_constraints"]
    technicians = payload["technicians"]

    spare_parts = payload.get("spare_parts",[])
    shift_details = payload.get("shift_details",{})

    user_prompt = build_user_prompt(
        work_orders,equipment_status,technicians,production_constraints,spare_parts,shift_details
    )

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role" : "user",
                "content" : user_prompt
            }
        ]
    )

    try:
        result = json.loads(response.content[0].text)
        return result

    except json.JSONDecodeError:
        raise ValueError("LLM did not return valid JSON. Please ensure the prompt requests JSON output.")


def build_user_prompt(work_orders,equipment_status,technicians,production_constraints,spare_parts,shift_details) -> str:

    """Takes in the arguments and create user prompt"""

    prompt = f"""

With the help of the following constraints, generate an optimized plan for refinery

work orders:
{work_orders}

equiment status:
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

    return prompt