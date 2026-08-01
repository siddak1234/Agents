"""Direct unit tests on the logic modules, no subprocess involved."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classifier import classify_documents
from evidence_rules import check_missing_evidence
from scoring import compute_readiness_score
from timeline import build_timeline


class TestClassifier(unittest.TestCase):
    def test_recognizes_fir(self):
        docs = [{"id": "d1", "text": "First Information Report FIR No. 45/2024"}]
        result = classify_documents(docs)
        self.assertEqual(result[0]["type"], "fir")
        self.assertGreater(result[0]["confidence"], 0.5)

    def test_unrecognized_text_returns_other(self):
        docs = [{"id": "d1", "text": "This is unrelated filler text."}]
        result = classify_documents(docs)
        self.assertEqual(result[0]["type"], "other")


class TestEvidenceRules(unittest.TestCase):
    def test_flags_missing_forensic_report_when_weapon_mentioned(self):
        docs = [{"id": "d1", "text": "A knife was recovered from the scene."}]
        classification = [{"id": "d1", "type": "fir", "confidence": 0.9}]
        missing = check_missing_evidence(docs, classification)
        expected_types = {m["expected"] for m in missing}
        self.assertIn("forensic_report", expected_types)

    def test_no_flag_when_evidence_type_already_present(self):
        docs = [{"id": "d1", "text": "A knife was recovered from the scene."}]
        classification = [{"id": "d1", "type": "forensic_report", "confidence": 0.9}]
        missing = check_missing_evidence(docs, classification)
        self.assertEqual(missing, [])


class TestTimeline(unittest.TestCase):
    def test_flags_evidence_dated_before_fir(self):
        docs = [
            {"id": "fir1", "text": "FIR filed on 2024-01-10."},
            {"id": "med1", "text": "Medical report dated 2024-01-05."},
        ]
        classification = [
            {"id": "fir1", "type": "fir", "confidence": 0.9},
            {"id": "med1", "type": "medical_report", "confidence": 0.9},
        ]
        _, issues = build_timeline(docs, classification)
        self.assertTrue(any(i["type"] == "date_conflict" for i in issues))


class TestScoring(unittest.TestCase):
    def test_perfect_case_scores_100(self):
        score = compute_readiness_score([], [], [])
        self.assertEqual(score, 100)

    def test_score_never_negative(self):
        missing = [{"expected": "x", "reason": "r", "triggered_by": "d"}] * 20
        score = compute_readiness_score([], [], missing)
        self.assertEqual(score, 0)


if __name__ == "__main__":
    unittest.main()