"""Runs the agent the way the orchestrator does — as a subprocess."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent


def call(request: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, "agent_main.py"],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        cwd=AGENT_DIR,
    )
    assert proc.returncode == 0, f"non-zero exit, stderr:\n{proc.stderr}"
    return json.loads(proc.stdout)  # fails if anything else was printed


class TestDescribe(unittest.TestCase):
    def test_describe_matches_manifest(self):
        envelope = call({"protocol": "agentcall/v1", "capability": "describe", "input": {}})
        self.assertTrue(envelope["ok"])
        names = {c["name"] for c in envelope["output"]["capabilities"]}
        self.assertEqual(names, {"describe", "review_case"})


class TestProtocolErrors(unittest.TestCase):
    def test_malformed_json(self):
        proc = subprocess.run(
            [sys.executable, "agent_main.py"],
            input="not json {{{",
            capture_output=True, text=True, cwd=AGENT_DIR,
        )
        envelope = json.loads(proc.stdout)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_wrong_protocol(self):
        envelope = call({"protocol": "wrong/v1", "capability": "describe", "input": {}})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_unknown_capability(self):
        envelope = call({"protocol": "agentcall/v1", "capability": "does_not_exist", "input": {}})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_missing_capability_field(self):
        envelope = call({"protocol": "agentcall/v1", "input": {}})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")


class TestReviewCaseValidation(unittest.TestCase):
    def test_missing_case_type(self):
        envelope = call({
            "protocol": "agentcall/v1", "capability": "review_case",
            "input": {"documents": [{"id": "d1", "text": "some text"}]},
        })
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_empty_documents(self):
        envelope = call({
            "protocol": "agentcall/v1", "capability": "review_case",
            "input": {"case_type": "criminal", "documents": []},
        })
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_document_missing_text(self):
        envelope = call({
            "protocol": "agentcall/v1", "capability": "review_case",
            "input": {"case_type": "criminal", "documents": [{"id": "d1"}]},
        })
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")


class TestReviewCaseSuccess(unittest.TestCase):
    def test_happy_path_returns_all_fields(self):
        envelope = call({
            "protocol": "agentcall/v1", "capability": "review_case",
            "input": {
                "case_type": "criminal",
                "documents": [
                    {"id": "d1", "text": "FIR No. 123 dated 2024-01-05. Weapon recovered at scene."},
                    {"id": "d2", "text": "Medical examination report dated 2024-01-02."},
                ],
            },
        })
        self.assertTrue(envelope["ok"])
        output = envelope["output"]
        for field in (
            "readiness_score", "document_classification", "timeline",
            "timeline_issues", "missing_evidence", "recommendations",
        ):
            self.assertIn(field, output)
        self.assertIsInstance(output["readiness_score"], int)
        self.assertTrue(0 <= output["readiness_score"] <= 100)


if __name__ == "__main__":
    unittest.main()