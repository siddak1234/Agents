"""Tests run the real entrypoint as a subprocess — the way the
orchestrator calls it — so stdout hygiene and the exit code are covered,
not assumed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

AGENT_MAIN = Path(__file__).resolve().parent.parent / "agent_main.py"


def call(request: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, str(AGENT_MAIN)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"non-zero exit; stderr={proc.stderr!r}"
    return json.loads(proc.stdout)  # fails if anything else was printed


def call_raw(raw_stdin: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(AGENT_MAIN)],
        input=raw_stdin,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"non-zero exit; stderr={proc.stderr!r}"
    return json.loads(proc.stdout)


class TestDescribe(unittest.TestCase):
    def test_describe_matches_manifest(self):
        envelope = call({"protocol": "agentcall/v1", "capability": "describe", "input": {}})
        self.assertTrue(envelope["ok"])
        output = envelope["output"]
        self.assertEqual(output["name"], "readability-grader")
        self.assertEqual(output["protocol"], "agentcall/v1")
        names = {c["name"] for c in output["capabilities"]}
        self.assertEqual(names, {"describe", "grade_text"})


class TestProtocolValidation(unittest.TestCase):
    def test_malformed_json(self):
        envelope = call_raw("not json at all")
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_request_not_object(self):
        envelope = call_raw("[1, 2, 3]")
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_wrong_protocol(self):
        envelope = call({"protocol": "something/v0", "capability": "grade_text", "input": {}})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_missing_capability(self):
        envelope = call({"protocol": "agentcall/v1", "input": {}})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_unknown_capability(self):
        envelope = call({"protocol": "agentcall/v1", "capability": "nope", "input": {}})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")
        self.assertIn("grade_text", envelope["error"]["message"])

    def test_input_not_object(self):
        envelope = call({"protocol": "agentcall/v1", "capability": "grade_text", "input": "nope"})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")


class TestGradeTextValidation(unittest.TestCase):
    def test_missing_text(self):
        envelope = call({"protocol": "agentcall/v1", "capability": "grade_text", "input": {}})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")
        self.assertIn("text", envelope["error"]["message"])

    def test_empty_text(self):
        envelope = call(
            {"protocol": "agentcall/v1", "capability": "grade_text", "input": {"text": "   "}}
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_text_not_string(self):
        envelope = call(
            {"protocol": "agentcall/v1", "capability": "grade_text", "input": {"text": 12345}}
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_no_words_when_only_symbols(self):
        envelope = call(
            {"protocol": "agentcall/v1", "capability": "grade_text", "input": {"text": "###???!!!"}}
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_no_detectable_sentences(self):
        envelope = call(
            {
                "protocol": "agentcall/v1",
                "capability": "grade_text",
                "input": {"text": "hello world this is a test with no terminators"},
            }
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_target_grade_not_a_number(self):
        envelope = call(
            {
                "protocol": "agentcall/v1",
                "capability": "grade_text",
                "input": {"text": "The cat sat on the mat.", "target_grade": "eight"},
            }
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")
        self.assertIn("target_grade", envelope["error"]["message"])


class TestGradeTextScoring(unittest.TestCase):
    def test_simple_sentence_scores_low_grade(self):
        # "The cat sat on the mat." — 6 words, 1 sentence, all 1-syllable
        # words. Hand-computed: avg_sentence_length=6, avg_syllables=1.0
        # grade = 0.39*6 + 11.8*1.0 - 15.59 = 2.34 + 11.8 - 15.59 = -1.45
        envelope = call(
            {
                "protocol": "agentcall/v1",
                "capability": "grade_text",
                "input": {"text": "The cat sat on the mat."},
            }
        )
        self.assertTrue(envelope["ok"])
        output = envelope["output"]
        self.assertEqual(output["word_count"], 6)
        self.assertEqual(output["sentence_count"], 1)
        self.assertAlmostEqual(output["flesch_kincaid_grade"], -1.45, delta=0.5)
        self.assertIsNone(output["meets_target"])
        self.assertIn(output["verdict"], {"very easy", "easy", "fairly easy"})

    def test_complex_sentence_scores_higher_grade(self):
        text = (
            "Photosynthesis is the process by which plants convert light "
            "energy into chemical energy."
        )
        envelope = call(
            {"protocol": "agentcall/v1", "capability": "grade_text", "input": {"text": text}}
        )
        self.assertTrue(envelope["ok"])
        output = envelope["output"]
        self.assertGreater(output["flesch_kincaid_grade"], 8)

    def test_meets_target_true_when_within_target(self):
        envelope = call(
            {
                "protocol": "agentcall/v1",
                "capability": "grade_text",
                "input": {"text": "The cat sat on the mat.", "target_grade": 5},
            }
        )
        self.assertTrue(envelope["ok"])
        self.assertTrue(envelope["output"]["meets_target"])

    def test_meets_target_false_when_above_target(self):
        text = (
            "Photosynthesis is the process by which plants convert light "
            "energy into chemical energy."
        )
        envelope = call(
            {
                "protocol": "agentcall/v1",
                "capability": "grade_text",
                "input": {"text": text, "target_grade": 3},
            }
        )
        self.assertTrue(envelope["ok"])
        self.assertFalse(envelope["output"]["meets_target"])

    def test_multi_sentence_counts(self):
        envelope = call(
            {
                "protocol": "agentcall/v1",
                "capability": "grade_text",
                "input": {"text": "Hello there. How are you? I am fine!"},
            }
        )
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["output"]["sentence_count"], 3)

    def test_usage_is_always_zero(self):
        envelope = call(
            {
                "protocol": "agentcall/v1",
                "capability": "grade_text",
                "input": {"text": "The cat sat on the mat."},
            }
        )
        self.assertEqual(
            envelope["usage"], {"input_tokens": 0, "output_tokens": 0, "model": None}
        )


if __name__ == "__main__":
    unittest.main()
