"""Unit tests for the Operations Maintenance Agent.

No network and no keys: `planner.call_llm` is patched at the module seam and
`_dispatch` runs in-process, so these tests cost nothing and cannot reach
Anthropic even on a machine where a real key is exported.
"""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

import anthropic
import httpx

import agentcall
import planner

PROTOCOL = "agentcall/v1"

VALID_INPUT = {
    "work_orders": [{"id": "WO-1", "equipment": "PUMP-204"}],
    "equipment_status": [{"id": "PUMP-204", "condition": "degraded"}],
    "technicians": [{"id": "T-1", "skills": ["mechanical"]}],
    "production_constraints": {"min_throughput_bpd": 90000},
}

VALID_PLAN = {
    "plan_summary": "Replace the PUMP-204 seal this shift; defer HX-33.",
    "scheduled_tasks": [{"work_order_id": "WO-1"}],
    "deferred_tasks": [],
    "risks": [{"description": "COMP-11 vibration trending up"}],
    "recommendations": [],
}


class _Block:
    """A stand-in for anthropic's ToolUseBlock.

    A MagicMock is wrong here: `planner._tool_payload` matches on `.type` and
    `.name`, and a MagicMock answers every attribute, so it would match blocks
    the real code would reject.
    """

    def __init__(self, type_: str, name: str | None = None, input_: dict | None = None) -> None:
        self.type = type_
        self.name = name
        self.input = input_


class _Usage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Response:
    def __init__(self, content: list, input_tokens: int = 1000, output_tokens: int = 500) -> None:
        self.content = content
        self.usage = _Usage(input_tokens, output_tokens)


def plan_response(plan: dict | None = None, **kw) -> _Response:
    payload = VALID_PLAN if plan is None else plan
    return _Response([_Block("tool_use", planner.TOOL_NAME, payload)], **kw)


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

    def test_describe_matches_the_manifest_wording(self) -> None:
        # Two hand-maintained copies of every capability description exist —
        # agent.yaml and CAPABILITIES here. Nothing in the platform compares
        # them, so this does.
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "agent.yaml"), encoding="utf-8") as fh:
            manifest = fh.read()

        for name, description in agentcall.CAPABILITIES:
            self.assertIn(description, " ".join(manifest.split()), f"{name} description drifted")


class TestInputValidation(unittest.TestCase):
    """A caller's mistake is invalid_request, and never reaches the model."""

    def test_missing_required_field_is_invalid_request(self) -> None:
        payload = {k: v for k, v in VALID_INPUT.items() if k != "production_constraints"}

        envelope = call("generate_maintenance_plan", payload)

        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")
        self.assertIn("production_constraints", envelope["error"]["message"])

    def test_wrong_type_is_invalid_request_and_never_calls_the_model(self) -> None:
        payload = {**VALID_INPUT, "work_orders": "not-a-list"}

        with patch("planner.call_llm") as mock_llm:
            envelope = call("generate_maintenance_plan", payload)

        mock_llm.assert_not_called()
        self.assertEqual(envelope["error"]["type"], "invalid_request")
        self.assertIn("work_orders", envelope["error"]["message"])

    def test_wrong_type_on_optional_field_is_rejected(self) -> None:
        payload = {**VALID_INPUT, "spare_parts": {"not": "an array"}}

        with patch("planner.call_llm") as mock_llm:
            envelope = call("generate_maintenance_plan", payload)

        mock_llm.assert_not_called()
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_oversized_input_is_rejected_before_the_model_is_called(self) -> None:
        payload = {**VALID_INPUT, "work_orders": [{"id": "WO-1"}] * 30_000}

        with patch("planner.call_llm") as mock_llm:
            envelope = call("generate_maintenance_plan", payload)

        mock_llm.assert_not_called()
        self.assertEqual(envelope["error"]["type"], "invalid_request")
        self.assertIn("split the work orders", envelope["error"]["message"])

    def test_a_rejected_key_is_unavailable_not_internal(self) -> None:
        rejected = anthropic.AuthenticationError(
            "invalid x-api-key",
            response=httpx.Response(401, request=httpx.Request("POST", "https://x")),
            body=None,
        )
        with patch("planner._create", side_effect=rejected), patch.dict(
            os.environ, {"ANTHROPIC_API_KEY": "sk-ant-rejected"}
        ):
            envelope = call("generate_maintenance_plan", VALID_INPUT)

        # A key that exists but does not work is still a config problem.
        self.assertEqual(envelope["error"]["type"], "unavailable")
        self.assertFalse(envelope["error"]["retryable"])

    def test_missing_credential_is_unavailable(self) -> None:
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            envelope = call("generate_maintenance_plan", VALID_INPUT)

        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "unavailable")
        self.assertFalse(envelope["error"]["retryable"])


class TestMaintenancePlanner(unittest.TestCase):
    """The happy path, and what the model can do wrong."""

    def test_generates_plan(self) -> None:
        with patch("planner.call_llm", return_value=plan_response()):
            envelope = call("generate_maintenance_plan", VALID_INPUT)

        self.assertTrue(envelope["ok"], envelope["error"])
        for field in ("plan_summary", "scheduled_tasks", "deferred_tasks", "risks",
                      "recommendations"):
            self.assertIn(field, envelope["output"])

    def test_reports_real_token_counts_and_cost(self) -> None:
        with patch("planner.call_llm", return_value=plan_response()):
            envelope = call("generate_maintenance_plan", VALID_INPUT)

        self.assertEqual(
            envelope["usage"],
            {"input_tokens": 1000, "output_tokens": 500, "model": planner.MODEL},
        )

    def test_model_output_failure_is_internal_not_the_callers_fault(self) -> None:
        # The model answered in prose instead of calling the tool.
        response = _Response([_Block("text")])

        with patch("planner.call_llm", return_value=response):
            envelope = call("generate_maintenance_plan", VALID_INPUT)

        self.assertFalse(envelope["ok"])
        # Not invalid_request: the caller's request was valid. `retryable` is
        # the value docs/AGENT_PROTOCOL.md gives `internal`.
        self.assertEqual(envelope["error"]["type"], "internal")
        self.assertFalse(envelope["error"]["retryable"])

    def test_model_failure_still_reports_what_it_cost(self) -> None:
        response = _Response([_Block("text")], input_tokens=1000, output_tokens=500)

        with patch("planner.call_llm", return_value=response):
            envelope = call("generate_maintenance_plan", VALID_INPUT)

        # The call was billed before its output could be inspected.
        self.assertEqual(envelope["usage"]["input_tokens"], 1000)
        self.assertEqual(envelope["usage"]["model"], planner.MODEL)

    def test_wrongly_typed_plan_is_not_reported_as_success(self) -> None:
        bad = {**VALID_PLAN, "risks": "vibration on COMP-11"}

        with patch("planner.call_llm", return_value=plan_response(bad)):
            envelope = call("generate_maintenance_plan", VALID_INPUT)

        self.assertFalse(envelope["ok"], "a string `risks` violates the declared output_schema")
        self.assertEqual(envelope["error"]["type"], "internal")

    def test_plan_missing_a_declared_field_is_not_success(self) -> None:
        bad = {k: v for k, v in VALID_PLAN.items() if k != "scheduled_tasks"}

        with patch("planner.call_llm", return_value=plan_response(bad)):
            envelope = call("generate_maintenance_plan", VALID_INPUT)

        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "internal")

    def test_another_tools_block_is_not_mistaken_for_a_plan(self) -> None:
        response = _Response([_Block("tool_use", "some_other_tool", {"anything": True})])

        with patch("planner.call_llm", return_value=response):
            envelope = call("generate_maintenance_plan", VALID_INPUT)

        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "internal")


class TestStructuredOutput(unittest.TestCase):
    """The tool schema is the output contract; keep it and the manifest aligned."""

    def test_tool_schema_requires_every_manifest_output_field(self) -> None:
        required = set(planner.PLAN_TOOL["input_schema"]["required"])

        self.assertEqual(
            required,
            {"plan_summary", "scheduled_tasks", "deferred_tasks", "risks", "recommendations"},
        )

    def test_the_call_forces_the_tool(self) -> None:
        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return plan_response()

        with (
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-not-real"}),
            patch("anthropic.Anthropic") as client_cls,
        ):
            client_cls.return_value.messages.create.side_effect = fake_create
            planner.call_llm("prompt")

        self.assertEqual(captured["tool_choice"], {"type": "tool", "name": planner.TOOL_NAME})
        self.assertEqual(captured["temperature"], 0.0)
        self.assertEqual(captured["model"], planner.MODEL)


class TestUsage(unittest.TestCase):
    def test_usage_names_the_model_and_reports_no_money(self) -> None:
        usage = planner.usage_for("some-future-model", 1_000_000, 0)

        self.assertEqual(usage["model"], "some-future-model")
        self.assertNotIn("cost_micros", usage)


if __name__ == "__main__":
    unittest.main()
