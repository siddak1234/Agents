"""Direct unit tests for scoring and extraction helpers.

These import analysis/recommendation modules in-process with synthetic
inputs — no subprocess, no network, no credentials. They exist so a
regression in classification or scoring (e.g. always-Low risk, swapped
Undervalued/Overvalued) fails a test instead of sailing through the
envelope-only suite.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# unittest discover runs with cwd = the agent folder; make sure the same
# import path agent_main.py uses is available when invoked other ways.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import budget as budget_mod
from budget import DeadlineBudget
from recommendation import MAX_EVIDENCE_BYTES, _enforce_evidence_size, _validate_output


class TestValidateOutput(unittest.TestCase):
    def test_happy_path(self):
        out = _validate_output(
            {
                "recommendation": "BUY",
                "confidence_percent": 72,
                "key_strengths": ["location"],
                "key_concerns": ["flood risk"],
                "recommended_offer_price_inr": 9_000_000,
            }
        )
        self.assertEqual(out["recommendation"], "BUY")
        self.assertEqual(out["confidence_percent"], 72)
        self.assertEqual(out["recommended_offer_price_inr"], 9_000_000)

    def test_off_enum_recommendation_is_reported_not_invented(self):
        # Used to return NEGOTIATE at 50%, ok:true — an answer the model
        # never gave, indistinguishable from one it did.
        with self.assertRaises(ConnectionError) as ctx:
            _validate_output({"recommendation": "MAYBE", "confidence_percent": 40})
        self.assertIn("MAYBE", str(ctx.exception))

    def test_missing_recommendation_is_reported(self):
        with self.assertRaises(ConnectionError):
            _validate_output({"confidence_percent": 40})

    def test_missing_or_non_numeric_confidence_is_reported(self):
        with self.assertRaises(ConnectionError):
            _validate_output({"recommendation": "BUY"})
        with self.assertRaises(ConnectionError):
            _validate_output({"recommendation": "BUY", "confidence_percent": "nope"})
        with self.assertRaises(ConnectionError):
            _validate_output({"recommendation": "BUY", "confidence_percent": True})

    def test_confidence_clamped_into_range(self):
        # A number the model did supply is kept and clamped, not discarded.
        self.assertEqual(_validate_output({"recommendation": "BUY", "confidence_percent": 150})["confidence_percent"], 100)
        self.assertEqual(_validate_output({"recommendation": "BUY", "confidence_percent": -5})["confidence_percent"], 0)
        self.assertEqual(_validate_output({"recommendation": "BUY", "confidence_percent": 72.6})["confidence_percent"], 72)

    def test_offer_price_rejects_bool_and_non_numeric(self):
        base = {"recommendation": "BUY", "confidence_percent": 60}
        self.assertIsNone(_validate_output({**base, "recommended_offer_price_inr": True})["recommended_offer_price_inr"])
        self.assertIsNone(_validate_output({**base, "recommended_offer_price_inr": "cheap"})["recommended_offer_price_inr"])
        self.assertIsNone(_validate_output(base)["recommended_offer_price_inr"])


class TestEvidenceSizeBound(unittest.TestCase):
    def test_normal_evidence_passes(self):
        _enforce_evidence_size({"financial_score": 7.5, "overall_risk": "Low"})

    def test_oversized_evidence_is_rejected(self):
        huge = {"blob": "x" * (MAX_EVIDENCE_BYTES + 1)}
        with self.assertRaises(ValueError) as ctx:
            _enforce_evidence_size(huge)
        self.assertIn("maximum is", str(ctx.exception))


class TestDeadlineBudget(unittest.TestCase):
    def test_default_when_deadline_omitted(self):
        budget = DeadlineBudget.from_deadline_ms(None)
        self.assertAlmostEqual(budget.remaining(), 180.0, delta=0.5)

    def test_invalid_deadline_rejected(self):
        with self.assertRaises(ValueError):
            DeadlineBudget.from_deadline_ms(0)
        with self.assertRaises(ValueError):
            DeadlineBudget.from_deadline_ms("soon")

    def test_slices_share_remaining_and_respect_cap(self):
        now = 1_000_000.0
        budget = DeadlineBudget(30.0, started=now)
        # with 30s left and 3 calls, each slice is min(12, 30*0.95/3) = 9.5
        real_mono = budget_mod.time.monotonic
        budget_mod.time.monotonic = lambda: now
        try:
            # 30s across 3 calls: each gets a share, none hits the ceiling.
            self.assertAlmostEqual(budget.for_call(3), 9.5, places=2)
            self.assertAlmostEqual(budget.for_call(2), 14.25, places=2)
            self.assertAlmostEqual(budget.for_call(1), 28.5, places=2)
        finally:
            budget_mod.time.monotonic = real_mono

    def test_shrinking_remaining_shrinks_slice(self):
        now = 1_000_000.0
        budget = DeadlineBudget(30.0, started=now - 20.0)  # 10s left
        real_mono = budget_mod.time.monotonic
        budget_mod.time.monotonic = lambda: now
        try:
            self.assertAlmostEqual(budget.for_call(2), 4.75, places=2)
        finally:
            budget_mod.time.monotonic = real_mono

    def test_exhausted_budget_times_out(self):
        now = 1_000_000.0
        budget = DeadlineBudget(0.1, started=now - 1.0)
        real_mono = budget_mod.time.monotonic
        budget_mod.time.monotonic = lambda: now
        try:
            with self.assertRaises(TimeoutError):
                budget.for_call(1)
        finally:
            budget_mod.time.monotonic = real_mono


if __name__ == "__main__":
    unittest.main()
