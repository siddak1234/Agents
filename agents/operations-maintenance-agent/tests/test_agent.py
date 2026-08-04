"""Unit tests for the Operations Maintenance Agent."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

AGENT_DIR = Path(__file__).resolve().parent.parent
ENTRYPOINT = AGENT_DIR / "agent_main.py"
PROTOCOL = "agentcall/v1"


def call(capability: str, payload: dict | None = None) -> dict:
    """Invoke the agent as a subprocess and return its envelope."""

    request = json.dumps(
        {
            "protocol": PROTOCOL,
            "capability": capability,
            "input": payload or {},
        }
    )

    proc = subprocess.run(
        [sys.executable, str(ENTRYPOINT)],
        input=request,
        capture_output=True,
        text=True,
        cwd=AGENT_DIR,
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0, f"Agent exited with {proc.returncode}\n{proc.stderr}"

    return json.loads(proc.stdout)


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

    @patch("planner.Anthropic")
    def test_generates_plan(self, mock_anthropic):
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

        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response
        mock_anthropic.return_value = mock_client

        payload = {
            "work_orders": [],
            "equipment_status": [],
            "technicians": [],
            "production_constraints": {},
        }

        envelope = call("generate_maintenance_plan", payload)

        import os

        if os.getenv("ANTHROPIC_API_KEY"):
            self.assertTrue(envelope["ok"], envelope["error"])

            self.assertIn("plan_summary", envelope["output"])
            self.assertIn("scheduled_tasks", envelope["output"])
            self.assertIn("deferred_tasks", envelope["output"])
            self.assertIn("risks", envelope["output"])
            self.assertIn("recommendations", envelope["output"])
        else:
            self.assertFalse(envelope["ok"])
            self.assertEqual(envelope["error"]["type"], "unavailable")

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
