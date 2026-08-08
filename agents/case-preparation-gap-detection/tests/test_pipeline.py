"""Direct unit tests on the logic modules, no subprocess involved."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import ClassVar

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

    def test_a_date_of_birth_does_not_become_the_documents_date(self):
        """The event filter has to gate the conflict check, not just the display.

        Applied only to the visible timeline, a date of birth still reached
        the comparison as the document's earliest date — so a post-mortem
        listing the deceased's DOB was reported as conflicting with the FIR
        while the timeline beside it showed the real examination date. The
        same response asserted two different dates for one document.
        """
        docs = [
            {"id": "fir1", "text": "FIR No. 7 filed on 2024-01-10. Incident at the market."},
            {"id": "pm1", "text": (
                "Post-mortem report. Deceased date of birth 1970-05-01. "
                "Post-mortem examination of the deceased conducted on 2024-01-15."
            )},
        ]
        classification = [
            {"id": "fir1", "type": "fir", "confidence": 0.9},
            {"id": "pm1", "type": "post_mortem_report", "confidence": 0.9},
        ]
        timeline, issues = build_timeline(docs, classification)

        assert not [i for i in issues if i["type"] == "date_conflict"], (
            "the post-mortem is dated after the FIR; the DOB is not its date"
        )
        assert "1970-05-01" not in [e["date"] for e in timeline]


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


class TestDedupeKeepsFindingsDistinct(unittest.TestCase):
    """Three rules expect a forensic_report; merging on the type alone lost two.

    Keying on `expected` only kept the first rule's reason and matched keyword
    and then attributed them to every triggering document — so a witness
    statement about CCTV was reported as having matched "knife", and two
    entirely different case files produced byte-identical output.
    """

    CLS: ClassVar[list[dict[str, str]]] = [{"doc_id": "d1", "type": "fir"}]

    def _forensic(self, docs):
        return [
            item
            for item in check_missing_evidence(docs, self.CLS)
            if item["expected"] == "forensic_report"
        ]

    def test_three_different_triggers_stay_three_findings(self):
        found = self._forensic([
            {"id": "d1", "text": "A knife was recovered."},
            {"id": "d2", "text": "CCTV footage exists."},
            {"id": "d3", "text": "Blood was found on the floor."},
        ])
        self.assertEqual(len(found), 3)
        for item in found:
            # each finding names only the document that actually triggered it
            self.assertEqual(len(item["triggered_by"]), 1)
            self.assertIn(item["matched"], {"knife", "cctv", "blood"})

    def test_the_same_trigger_in_three_documents_stays_one_finding(self):
        found = self._forensic([
            {"id": "d1", "text": "A knife was recovered."},
            {"id": "d2", "text": "The knife was bagged."},
            {"id": "d3", "text": "Knife sent to store."},
        ])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["triggered_by"], ["d1", "d2", "d3"])

    def test_two_different_case_files_do_not_produce_identical_output(self):
        a = self._forensic([
            {"id": "d1", "text": "A knife was recovered."},
            {"id": "d2", "text": "CCTV footage exists."},
        ])
        b = self._forensic([
            {"id": "d1", "text": "A knife was recovered."},
            {"id": "d2", "text": "The knife was bagged."},
        ])
        self.assertNotEqual(a, b)


class TestUnparsedDatesAreReported(unittest.TestCase):
    """A date-shaped string that fails to parse used to vanish without trace."""

    def test_a_month_first_date_is_reported_not_dropped(self):
        _timeline, issues = build_timeline(
            [
                {"id": "fir1", "text": "FIR filed on 2024-03-01."},
                {"id": "w1", "text": "I saw the accused on 12/25/2024."},
            ],
            [{"id": "fir1", "type": "fir"}, {"id": "w1", "type": "witness_statement"}],
        )
        unparsed = [i for i in issues if i["type"] == "unparsed_date"]
        self.assertEqual(len(unparsed), 1)
        self.assertIn("12/25/2024", unparsed[0]["description"])
        self.assertEqual(unparsed[0]["docs"], ["w1"])

    def test_a_valid_day_first_date_raises_no_issue(self):
        _timeline, issues = build_timeline(
            [{"id": "fir1", "text": "FIR filed on 15/03/2024."}],
            [{"id": "fir1", "type": "fir"}],
        )
        self.assertEqual([i for i in issues if i["type"] == "unparsed_date"], [])
