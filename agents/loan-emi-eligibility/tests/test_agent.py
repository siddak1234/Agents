"""Tests that call agent_main.py the way the orchestrator does: as a
subprocess reading one JSON request on stdin and writing one JSON envelope
on stdout. No network access is used anywhere in this suite.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

AGENT_PATH = Path(__file__).resolve().parent.parent / "agent_main.py"
PROTOCOL = "agentcall/v1"


def call(capability: str, input_payload: dict | None = None, protocol: str | None = PROTOCOL) -> dict:
    request = {
        "protocol": protocol,
        "capability": capability,
        "input": input_payload if input_payload is not None else {},
        "request_id": "test",
        "deadline_ms": 5000,
    }
    proc = subprocess.run(
        [sys.executable, str(AGENT_PATH)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        cwd=AGENT_PATH.parent,
    )
    assert proc.returncode == 0, f"non-zero exit: {proc.returncode}, stderr={proc.stderr}"
    return json.loads(proc.stdout)  # fails if anything else was printed to stdout


class TestDescribe(unittest.TestCase):
    def test_describe_matches_manifest_capability_names(self):
        envelope = call("describe")
        self.assertTrue(envelope["ok"])
        names = {c["name"] for c in envelope["output"]["capabilities"]}
        self.assertEqual(names, {"describe", "calculate_emi", "check_eligibility"})
        self.assertEqual(envelope["output"]["name"], "loan-emi-eligibility")
        self.assertEqual(envelope["output"]["protocol"], PROTOCOL)


class TestCalculateEmi(unittest.TestCase):
    def test_known_value(self):
        # P=100000, annual rate=12%, tenure=12 months -> standard EMI formula
        envelope = call("calculate_emi", {
            "principal": 100000,
            "annual_rate_percent": 12,
            "tenure_months": 12,
        })
        self.assertTrue(envelope["ok"])
        out = envelope["output"]
        self.assertAlmostEqual(out["emi"], 8884.88, places=1)
        self.assertGreater(out["total_interest"], 0)
        self.assertAlmostEqual(out["total_payment"], out["emi"] * 12, places=1)

    def test_negative_principal_is_invalid_request(self):
        envelope = call("calculate_emi", {
            "principal": -5000,
            "annual_rate_percent": 10,
            "tenure_months": 12,
        })
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")
        self.assertFalse(envelope["error"]["retryable"])

    def test_zero_tenure_is_invalid_request(self):
        envelope = call("calculate_emi", {
            "principal": 5000,
            "annual_rate_percent": 10,
            "tenure_months": 0,
        })
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_missing_field_is_invalid_request(self):
        envelope = call("calculate_emi", {"principal": 5000})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_would_fail_if_capability_deleted(self):
        # Asserts on the actual computed numeric result, not just ok=True,
        # so this test fails if calculate_emi is removed or stubbed out.
        envelope = call("calculate_emi", {
            "principal": 50000,
            "annual_rate_percent": 9,
            "tenure_months": 6,
        })
        self.assertTrue(envelope["ok"])
        self.assertGreater(envelope["output"]["emi"], 8000)
        self.assertLess(envelope["output"]["emi"], 8600)


class TestCheckEligibility(unittest.TestCase):
    def test_eligible_case(self):
        envelope = call("check_eligibility", {
            "monthly_income": 100000,
            "existing_emis": 5000,
            "requested_principal": 200000,
            "annual_rate_percent": 10,
            "tenure_months": 24,
            "applicant_tier": "standard",
        })
        self.assertTrue(envelope["ok"])
        out = envelope["output"]
        self.assertTrue(out["eligible"])
        self.assertEqual(out["foir_limit_percent"], 50.0)
        self.assertLessEqual(out["foir_percent"], out["foir_limit_percent"])

    def test_ineligible_case_reports_max_principal(self):
        envelope = call("check_eligibility", {
            "monthly_income": 30000,
            "existing_emis": 10000,
            "requested_principal": 500000,
            "annual_rate_percent": 14,
            "tenure_months": 12,
            "applicant_tier": "self_employed",
        })
        self.assertTrue(envelope["ok"])
        out = envelope["output"]
        self.assertFalse(out["eligible"])
        self.assertEqual(out["foir_limit_percent"], 40.0)
        self.assertIn("Maximum eligible principal", out["reason"])
        self.assertGreaterEqual(out["max_eligible_principal"], 0)

    def test_default_tier_is_standard(self):
        envelope = call("check_eligibility", {
            "monthly_income": 60000,
            "existing_emis": 0,
            "requested_principal": 100000,
            "annual_rate_percent": 10,
            "tenure_months": 12,
        })
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["output"]["foir_limit_percent"], 50.0)

    def test_unknown_tier_is_invalid_request(self):
        envelope = call("check_eligibility", {
            "monthly_income": 60000,
            "existing_emis": 0,
            "requested_principal": 100000,
            "annual_rate_percent": 10,
            "tenure_months": 12,
            "applicant_tier": "vip",
        })
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_negative_income_is_invalid_request(self):
        envelope = call("check_eligibility", {
            "monthly_income": -1,
            "existing_emis": 0,
            "requested_principal": 100000,
            "annual_rate_percent": 10,
            "tenure_months": 12,
        })
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")


class TestProtocolLevelErrors(unittest.TestCase):
    def test_unknown_capability(self):
        envelope = call("does_not_exist")
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_wrong_protocol(self):
        envelope = call("calculate_emi", {"principal": 1000, "annual_rate_percent": 10, "tenure_months": 6},
                         protocol="not-agentcall/v0")
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_malformed_json_on_stdin(self):
        proc = subprocess.run(
            [sys.executable, str(AGENT_PATH)],
            input="{not valid json",
            capture_output=True,
            text=True,
            cwd=AGENT_PATH.parent,
        )
        self.assertEqual(proc.returncode, 0)
        envelope = json.loads(proc.stdout)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")
        self.assertEqual(envelope["capability"], "")


if __name__ == "__main__":
    unittest.main()
