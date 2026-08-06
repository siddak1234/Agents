"""Tests for the investment-due-diligence agent.

These run the real entrypoint as a subprocess -- the same way the
orchestrator does -- so stdout hygiene (RULE 1) and the exit code
(RULE 2) are exercised, not assumed. None of these tests touch the
network: every case here either needs no external call, or fails
validation (a missing API key, or bad input) before one would be made.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
AGENT_MAIN = AGENT_DIR / "agent_main.py"


def call(
    capability: str,
    input_: dict | None = None,
    *,
    raw_stdin: str | None = None,
    env_overrides: dict[str, str] | None = None,
) -> dict:
    request = raw_stdin
    if request is None:
        request = json.dumps(
            {
                "protocol": "agentcall/v1",
                "capability": capability,
                "input": input_ or {},
                "request_id": "test",
                "deadline_ms": 30000,
            }
        )

    # Deliberately NOT inheriting the caller's environment -- these tests
    # assert on behavior with specific keys present or absent.
    env = {"PATH": os.environ.get("PATH", "")}
    if env_overrides:
        env.update(env_overrides)

    proc = subprocess.run(
        [sys.executable, str(AGENT_MAIN)],
        input=request,
        capture_output=True,
        text=True,
        cwd=str(AGENT_DIR),
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, f"non-zero exit: {proc.stderr}"
    assert proc.stdout.strip(), f"no stdout produced; stderr was: {proc.stderr}"
    # If anything else had been printed to stdout (RULE 1 violated), this
    # would fail to parse as a single JSON object.
    return json.loads(proc.stdout)


def _manifest_capability_names() -> set[str]:
    text = (AGENT_DIR / "agent.yaml").read_text()
    return set(re.findall(r"^\s*-\s*name:\s*(\S+)", text, re.MULTILINE))


class TestDescribe(unittest.TestCase):
    def test_matches_manifest(self):
        envelope = call("describe")
        self.assertTrue(envelope["ok"], envelope.get("error"))
        names = {c["name"] for c in envelope["output"]["capabilities"]}
        self.assertEqual(names, _manifest_capability_names())

    def test_costs_nothing_and_needs_no_keys(self):
        envelope = call("describe")
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["usage"], {"input_tokens": 0, "output_tokens": 0, "cost_micros": 0})


class TestProtocolHygiene(unittest.TestCase):
    def test_malformed_json_stdin(self):
        envelope = call("describe", raw_stdin="not json")
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")
        self.assertEqual(envelope["capability"], "")
        self.assertFalse(envelope["error"]["retryable"])

    def test_wrong_protocol(self):
        raw = json.dumps({"protocol": "other/v1", "capability": "describe", "input": {}})
        envelope = call("describe", raw_stdin=raw)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")
        self.assertEqual(envelope["capability"], "")

    def test_request_not_an_object(self):
        envelope = call("describe", raw_stdin="[1, 2, 3]")
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_missing_capability_field(self):
        raw = json.dumps({"protocol": "agentcall/v1", "input": {}})
        envelope = call("describe", raw_stdin=raw)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_input_not_an_object(self):
        raw = json.dumps({"protocol": "agentcall/v1", "capability": "describe", "input": "nope"})
        envelope = call("describe", raw_stdin=raw)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_unknown_capability(self):
        envelope = call("levitate_property")
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")
        self.assertIn("unknown capability", envelope["error"]["message"])
        self.assertIn("investment_recommendation", envelope["error"]["message"])


class TestPropertyIntelligence(unittest.TestCase):
    def test_missing_identifiers_is_invalid(self):
        envelope = call("property_intelligence", {"asking_price_inr": 1000000})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_succeeds_without_a_tavily_key(self):
        # Corroboration search is best-effort: no key degrades gracefully
        # instead of failing the capability.
        envelope = call(
            "property_intelligence",
            {
                "address": "Prestige Lakeside Habitat, Whitefield, Bangalore",
                "asking_price_inr": 14000000,
                "area_sqft": 1650,
            },
        )
        self.assertTrue(envelope["ok"], envelope.get("error"))
        self.assertEqual(envelope["output"]["location"], "Whitefield, Bangalore")
        self.assertEqual(envelope["output"]["builder"], "Prestige Lakeside Habitat")
        self.assertAlmostEqual(envelope["output"]["price_per_sqft"], 14000000 / 1650, places=2)

    def test_commercial_property_is_rejected(self):
        envelope = call(
            "property_intelligence",
            {"address": "Prime Commercial Office Space, Hitech City, Hyderabad"},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_wrong_type_for_asking_price(self):
        envelope = call(
            "property_intelligence",
            {"address": "Some Apartment, Whitefield, Bangalore", "asking_price_inr": "a lot"},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")


class TestFlexibleInput(unittest.TestCase):
    """Nothing but the bare minimum should be *required* -- everything
    else is optional context the caller may or may not have. These
    assert `unavailable` (reached the dependency check) rather than
    `invalid_request` (rejected before getting there), which is what
    proves the loosened validation actually let the partial payload
    through.
    """

    def test_financial_analysis_accepts_location_only(self):
        envelope = call("financial_analysis", {"location": "Whitefield, Bangalore"})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "unavailable")

    def test_financial_analysis_derives_price_from_total_and_area(self):
        envelope = call(
            "financial_analysis",
            {"location": "Whitefield, Bangalore", "asking_price_inr": 14000000, "area_sqft": 1650},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "unavailable")

    def test_risk_assessment_accepts_location_only(self):
        envelope = call("risk_assessment", {"location": "Whitefield, Bangalore"})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "unavailable")

    def test_recommendation_accepts_risk_assessment_object_alone(self):
        envelope = call(
            "investment_recommendation",
            {"risk_assessment": {"overall_risk": "Medium", "identified_risks": []}},
            env_overrides={"GROQ_API_KEY": "test-key-not-real"},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "unavailable")

    def test_recommendation_accepts_shortcut_scores_without_raw_objects(self):
        envelope = call(
            "investment_recommendation",
            {"financial_score": 7.5, "location_score": 8.0, "overall_risk": "Low"},
            env_overrides={"GROQ_API_KEY": "test-key-not-real"},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "unavailable")

    def test_recommendation_rejects_completely_empty_evidence(self):
        envelope = call(
            "investment_recommendation",
            {},
            env_overrides={"GROQ_API_KEY": "test-key-not-real"},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")


class TestMissingCredentials(unittest.TestCase):
    """TAVILY_API_KEY / GROQ_API_KEY absent -> unavailable, not a crash."""

    def test_financial_analysis(self):
        envelope = call(
            "financial_analysis",
            {"location": "Whitefield, Bangalore", "price_per_sqft": 8485, "asking_price_inr": 14000000},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "unavailable")
        self.assertFalse(envelope["error"]["retryable"])

    def test_location_infrastructure_analysis(self):
        envelope = call("location_infrastructure_analysis", {"location": "Whitefield, Bangalore"})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "unavailable")

    def test_risk_assessment(self):
        envelope = call(
            "risk_assessment",
            {"builder": "Prestige Group", "location": "Whitefield, Bangalore", "property_type": "Apartment"},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "unavailable")

    def test_investment_recommendation(self):
        envelope = call(
            "investment_recommendation",
            {
                "financial_score": 8.4,
                "location_score": 8.9,
                "overall_risk": "Medium",
                "property_profile": {"builder": "Prestige Group", "property_type": "Apartment"},
            },
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "unavailable")
        self.assertFalse(envelope["error"]["retryable"])


class TestInputValidation(unittest.TestCase):
    def test_financial_analysis_rejects_non_positive_price(self):
        envelope = call(
            "financial_analysis",
            {"location": "Whitefield, Bangalore", "price_per_sqft": 0, "asking_price_inr": 14000000},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_risk_assessment_requires_location(self):
        envelope = call("risk_assessment", {"builder": "Prestige Group", "property_type": "Apartment"})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_recommendation_rejects_non_object_property_profile(self):
        envelope = call(
            "investment_recommendation",
            {
                "financial_score": 8.4,
                "location_score": 8.9,
                "overall_risk": "Medium",
                "property_profile": "not-an-object",
            },
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_recommendation_rejects_bad_risk_level(self):
        envelope = call(
            "investment_recommendation",
            {
                "financial_score": 8.4,
                "location_score": 8.9,
                "overall_risk": "Catastrophic",
                "property_profile": {},
            },
            env_overrides={"GROQ_API_KEY": "test-key-not-real"},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")


if __name__ == "__main__":
    unittest.main()