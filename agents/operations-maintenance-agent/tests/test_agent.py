"""Unit tests for the Operations Maintenance Agent."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch, MagicMock

import agentcall

PROTOCOL = "agentcall/v1"

def call(capability: str, payload: dict | None = None) -> dict:
    request = json.dumps(
        {
            "protocol": PROTOCOL,
            "capability": capability,
            "input": payload or {},
        }
    )

    return agentcall._dispatch(request)


class TestContract(unittest.TestCase):
    """These contract tests should remain for every agent."""

    def test_describe_reports_name_and_capabilities(self) -> None:
        envelope = call("describe")

        self.assertTrue(envelope["ok"], envelope["error"])
        self.assertIn("name", envelope["output"])
        self.assertTrue(envelope["output"]["capabilities"])

    def test_unknown_capability_is_a_typed_error(self) -> None:
        envelope = call("no-such-capability")

        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_every_response_reports_usage(self) -> None:
        self.assertIn("usage", call("describe"))


class TestMaintenancePlanner(unittest.TestCase):
    """Tests for the maintenance planning capability."""

    @patch("planner.call_llm")
    def test_generates_plan(self, mock_call_llm):
        fake_response = MagicMock()
        fake_response.content = [
            MagicMock(
                text=json.dumps(
                    {
                        "plan_summary": "Plan generated successfully",
                        "scheduled_tasks": [],
                        "deferred_tasks": [],
                        "risks": [],
                        "recommendations": [],
                    }
                )
            )
        ]

        fake_response.usage = MagicMock(
            input_tokens=100,
            output_tokens=50,
        )

        mock_call_llm.return_value = fake_response

        payload = {
            "work_orders": [],
            "equipment_status": [],
            "technicians": [],
            "production_constraints": {},
        }

        envelope = call("generate_maintenance_plan", payload)

        self.assertTrue(envelope["ok"],envelope["error"])

        self.assertIn("plan_summary", envelope["output"])
        self.assertIn("scheduled_tasks", envelope["output"])
        self.assertIn("deferred_tasks", envelope["output"])
        self.assertIn("risks", envelope["output"])
        self.assertIn("recommendations", envelope["output"])
        

    def test_missing_required_field(self) -> None:
        payload = {
            "work_orders": [],
            "equipment_status": [],
            "technicians": [],
            # production_constraints intentionally omitted
        }

        envelope = call("generate_maintenance_plan", payload)

        self.assertFalse(envelope["ok"])


if __name__ == "__main__":
    unittest.main()
