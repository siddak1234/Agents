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


def call(
    capability: str, input_payload: dict | None = None, protocol: str | None = PROTOCOL
) -> dict:
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

    def test_absurd_tenure_is_invalid_request_not_internal_crash(self):
        # A caller's units mistake (e.g. days instead of months) must not
        # crash the process with an OverflowError reported as "internal" --
        # it's a caller-side mistake and belongs under invalid_request.
        envelope = call("calculate_emi", {
            "principal": 100000,
            "annual_rate_percent": 10,
            "tenure_months": 100000,
        })
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_total_payment_reconciles_with_rounded_emi(self):
        # total_payment must equal emi * tenure_months exactly, using the
        # *rounded* emi -- not a paisa off, so a caller checking the math
        # against the response gets a consistent answer.
        envelope = call("calculate_emi", {
            "principal": 100000,
            "annual_rate_percent": 12,
            "tenure_months": 12,
        })
        out = envelope["output"]
        self.assertEqual(out["total_payment"], round(out["emi"] * 12, 2))

    def test_infinite_principal_is_invalid_request(self):
        proc = subprocess.run(
            [sys.executable, str(AGENT_PATH)],
            input='{"protocol":"agentcall/v1","capability":"calculate_emi",'
                  '"input":{"principal":Infinity,"annual_rate_percent":10,'
                  '"tenure_months":12},"request_id":"","deadline_ms":5000}',
            capture_output=True,
            text=True,
            cwd=AGENT_PATH.parent,
        )
        envelope = json.loads(proc.stdout)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_nan_annual_rate_is_invalid_request(self):
        proc = subprocess.run(
            [sys.executable, str(AGENT_PATH)],
            input='{"protocol":"agentcall/v1","capability":"calculate_emi",'
                  '"input":{"principal":100000,"annual_rate_percent":NaN,'
                  '"tenure_months":12},"request_id":"","deadline_ms":5000}',
            capture_output=True,
            text=True,
            cwd=AGENT_PATH.parent,
        )
        envelope = json.loads(proc.stdout)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")


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

    def test_existing_emis_alone_flips_eligibility(self):
        # Same income/requested loan/rate/tenure; only existing_emis differs
        # enough to cross the standard 50% FOIR line by itself. If
        # existing_emis were ever dropped from the FOIR sum, both calls
        # would report the same (incorrect) eligible value.
        base = {
            "monthly_income": 100000,
            "requested_principal": 500000,
            "annual_rate_percent": 10,
            "tenure_months": 60,
            "applicant_tier": "standard",
        }
        low_existing = call("check_eligibility", {**base, "existing_emis": 5000})
        high_existing = call("check_eligibility", {**base, "existing_emis": 55000})

        self.assertTrue(low_existing["ok"])
        self.assertTrue(high_existing["ok"])
        self.assertTrue(low_existing["output"]["eligible"])
        self.assertFalse(high_existing["output"]["eligible"])

        # Hard-coded, independently computed FOIR: emi for 500000 @ 10% / 60mo
        # is 10623.51 (verified against the standard reducing-balance formula
        # outside this codebase). FOIR = (existing + emi) / income * 100.
        self.assertAlmostEqual(low_existing["output"]["foir_percent"], 15.62, places=1)
        self.assertAlmostEqual(high_existing["output"]["foir_percent"], 65.62, places=1)

    def test_applicant_tier_null_is_invalid_request_not_default(self):
        # Explicit null must be rejected, not silently relaxed to "standard".
        envelope = call("check_eligibility", {
            "monthly_income": 60000,
            "existing_emis": 0,
            "requested_principal": 100000,
            "annual_rate_percent": 10,
            "tenure_months": 12,
            "applicant_tier": None,
        })
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_applicant_tier_omitted_defaults_to_standard(self):
        # Contrast with the null case above: omitting the key is the only
        # way to get the default.
        envelope = call("check_eligibility", {
            "monthly_income": 60000,
            "existing_emis": 0,
            "requested_principal": 100000,
            "annual_rate_percent": 10,
            "tenure_months": 12,
        })
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["output"]["foir_limit_percent"], 50.0)


class TestProtocolLevelErrors(unittest.TestCase):
    def test_unknown_capability(self):
        envelope = call("does_not_exist")
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_wrong_protocol(self):
        payload = {"principal": 1000, "annual_rate_percent": 10, "tenure_months": 6}
        envelope = call("calculate_emi", payload, protocol="not-agentcall/v0")
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
