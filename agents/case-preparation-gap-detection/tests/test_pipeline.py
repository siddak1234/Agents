"""Direct unit tests on the logic modules, no subprocess involved."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classifier import classify_documents
from evidence_rules import check_missing_evidence
from scoring import compute_readiness_score
from timeline import _snippet, build_timeline


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

class TestTimelineFIRRuleScope(unittest.TestCase):
    def test_medical_report_before_fir_is_not_flagged(self):
        docs = [
            {"id": "fir1", "text": "FIR No. 7 filed 2024-01-10."},
            {"id": "med1", "text": "Medical examination report dated 2024-01-05."},
        ]
        classification = [
            {"id": "fir1", "type": "fir", "confidence": 0.9},
            {"id": "med1", "type": "medical_report", "confidence": 0.9},
        ]
        _, issues = build_timeline(docs, classification)
        self.assertEqual(issues, [])

    def test_post_mortem_before_fir_is_still_flagged(self):
        docs = [
            {"id": "fir1", "text": "FIR No. 7 filed 2024-01-10."},
            {"id": "pm1", "text": "Post-mortem report dated 2024-01-05."},
        ]
        classification = [
            {"id": "fir1", "type": "fir", "confidence": 0.9},
            {"id": "pm1", "type": "post_mortem_report", "confidence": 0.9},
        ]
        _, issues = build_timeline(docs, classification)
        self.assertTrue(any(i["type"] == "date_conflict" for i in issues))

class TestFIRFilingDate(unittest.TestCase):
    def test_a_date_of_birth_does_not_defeat_the_conflict_check(self):
        docs = [
            {"id": "fir1", "text": "FIR No. 7 filed 2024-05-10. Accused date of birth 1985-03-02."},
            {"id": "pm1", "text": "Post-mortem report dated 2024-01-01."},
        ]
        classification = [
            {"id": "fir1", "type": "fir", "confidence": 0.9},
            {"id": "pm1", "type": "post_mortem_report", "confidence": 0.9},
        ]
        _, issues = build_timeline(docs, classification)
        self.assertTrue(any(i["type"] == "date_conflict" for i in issues))

    def test_no_labeled_filing_date_produces_missing_date_not_silence(self):
        docs = [
            {"id": "fir1", "text": "FIR No. 7. Some incident occurred on 2024-05-10."},
        ]
        classification = [{"id": "fir1", "type": "fir", "confidence": 0.9}]
        _, issues = build_timeline(docs, classification)
        self.assertTrue(any(i["type"] == "missing_date" for i in issues))

    def test_slash_format_filing_date_is_found(self):
        docs = [
            {"id": "fir1", "text": "FIR filed on 10/02/2024."},
            {"id": "pm1", "text": "Post-mortem report dated 05/02/2024."},
        ]
        classification = [
            {"id": "fir1", "type": "fir", "confidence": 0.9},
            {"id": "pm1", "type": "post_mortem_report", "confidence": 0.9},
        ]
        _, issues = build_timeline(docs, classification)
        self.assertTrue(any(i["type"] == "date_conflict" for i in issues))

class TestClassifierScoring(unittest.TestCase):
    def test_post_mortem_wording_is_not_misclassified_as_medical_report(self):
        docs = [{
            "id": "d1",
            "text": "Post-mortem report: examination of the deceased "
                     "conducted to determine cause of death.",
        }]
        result = classify_documents(docs)
        self.assertEqual(result[0]["type"], "post_mortem_report")

    def test_a_witness_statement_is_classified_correctly_not_flagged_missing(self):
        docs = [{
            "id": "d1",
            "text": "I, Ram Kumar, state that I saw the accused leave "
                     "the building at 9pm.",
        }]
        classification = classify_documents(docs)
        self.assertEqual(classification[0]["type"], "witness_statement")

        missing = check_missing_evidence(docs, classification)
        expected_types = {m["expected"] for m in missing}
        self.assertNotIn("witness_statement", expected_types)

class TestEvidenceRulesWordBoundary(unittest.TestCase):
    def test_substring_inside_unrelated_word_does_not_trigger(self):
        docs = [{
            "id": "d1",
            "text": "The dispute had begun at the sawmill near Warsaw Road.",
        }]
        classification = [{"id": "d1", "type": "other", "confidence": 0.3}]
        missing = check_missing_evidence(docs, classification)
        expected_types = {m["expected"] for m in missing}
        self.assertNotIn("forensic_report", expected_types)
        self.assertEqual(missing, [])

    def test_no_flag_when_evidence_type_already_present(self):
        docs = [{"id": "d1", "text": "A knife was recovered from the scene."}]
        classification = [{"id": "d1", "type": "forensic_report", "confidence": 0.9}]
        missing = check_missing_evidence(docs, classification)
        self.assertEqual(missing, [])


class TestTimeline(unittest.TestCase):
    def test_flags_post_mortem_dated_before_fir(self):
        docs = [
            {"id": "fir1", "text": "FIR filed on 2024-01-10."},
            {"id": "pm1", "text": "Post-mortem report dated 2024-01-05."},
        ]
        classification = [
            {"id": "fir1", "type": "fir", "confidence": 0.9},
            {"id": "pm1", "type": "post_mortem_report", "confidence": 0.9},
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

    def test_missing_date_penalizes_less_than_a_real_conflict(self):
        missing_date_issue = [{"type": "missing_date"}]
        conflict_issue = [{"type": "date_conflict"}]
        score_missing = compute_readiness_score([], missing_date_issue, [])
        score_conflict = compute_readiness_score([], conflict_issue, [])
        self.assertGreater(score_missing, score_conflict)
class TestTimelineEventFiltering(unittest.TestCase):
    def test_date_of_birth_does_not_appear_as_a_timeline_event(self):
        docs = [{
            "id": "d1",
            "text": "FIR filed on 2024-01-10. Accused date of birth 1985-03-02.",
        }]
        classification = [{"id": "d1", "type": "fir", "confidence": 0.9}]
        timeline, _ = build_timeline(docs, classification)
        dates_shown = {entry["date"] for entry in timeline}
        self.assertNotIn("1985-03-02", dates_shown)
        self.assertIn("2024-01-10", dates_shown)

class TestSnippetWordBoundary(unittest.TestCase):
    def test_snippet_does_not_cut_mid_word(self):
        text = "Filed on 2024-03-03 the accused was arrested nearby at dawn."
        start = text.index("2024-03-03")
        end = start + len("2024-03-03")
        result = _snippet(text, start, end)
        # The character immediately before the snippet in the source text
        # must be whitespace (or the snippet starts at position 0) — i.e.
        # the snippet doesn't begin mid-word.
        idx = text.find(result)
        self.assertTrue(idx == 0 or text[idx - 1].isspace())
if __name__ == "__main__":
    unittest.main()
