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
from analysis import (
    DEFAULT_RENTAL_YIELD_PERCENT,
    FLOOD_KEYWORDS,
    MARKET_SLOWDOWN_KEYWORDS,
    NEGATIVE_BUILDER_KEYWORDS,
    _classify_growth,
    _classify_market_value,
    _combine_risk_levels,
    _count_keyword_hits,
    _extract_prices_per_sqft,
    _extract_rental_yield,
    _keyword_score,
    _score_financials,
)
from budget import DeadlineBudget
from clients import groq_cost_micros
from recommendation import MAX_EVIDENCE_BYTES, _enforce_evidence_size, _validate_output


def _hit(title: str = "", content: str = "") -> dict:
    return {"title": title, "content": content}


class TestClassifyMarketValue(unittest.TestCase):
    def test_undervalued_at_and_below_minus_five(self):
        self.assertEqual(_classify_market_value(-5), "Undervalued")
        self.assertEqual(_classify_market_value(-20), "Undervalued")

    def test_fair_between_thresholds(self):
        self.assertEqual(_classify_market_value(-4.9), "Fair")
        self.assertEqual(_classify_market_value(0), "Fair")
        self.assertEqual(_classify_market_value(8), "Fair")

    def test_overvalued_above_eight(self):
        self.assertEqual(_classify_market_value(8.1), "Overvalued")
        self.assertEqual(_classify_market_value(50), "Overvalued")


class TestScoreFinancials(unittest.TestCase):
    def test_baseline_default_yield_and_roi(self):
        # premium None, yield at default (3.0), ROI at 10 → score stays ~5.0
        self.assertEqual(_score_financials(None, DEFAULT_RENTAL_YIELD_PERCENT, 10.0), 5.0)

    def test_overpriced_lowers_score(self):
        cheap = _score_financials(-10, 3.0, 10.0)
        expensive = _score_financials(20, 3.0, 10.0)
        self.assertGreater(cheap, expensive)

    def test_clamped_to_zero_ten(self):
        self.assertEqual(_score_financials(100, 0.0, 0.0), 0.0)
        self.assertEqual(_score_financials(-100, 20.0, 50.0), 10.0)


class TestCombineRiskLevels(unittest.TestCase):
    def test_two_highs_is_high(self):
        self.assertEqual(_combine_risk_levels(["High", "High", "Low"]), "High")

    def test_single_high_wins_via_max(self):
        self.assertEqual(_combine_risk_levels(["High", "Low", "Medium"]), "High")

    def test_medium_over_low(self):
        self.assertEqual(_combine_risk_levels(["Medium", "Low", "Low"]), "Medium")

    def test_all_low(self):
        self.assertEqual(_combine_risk_levels(["Low", "Low", "Low"]), "Low")


class TestKeywordScore(unittest.TestCase):
    def test_no_hits_is_baseline_five(self):
        self.assertEqual(_keyword_score([_hit("quiet street")], ("metro", "airport")), 5.0)

    def test_each_hit_adds_one_capped_at_ten(self):
        results = [_hit("Metro and airport near the highway and railway bus stop")]
        keywords = ("metro", "highway", "airport", "railway", "bus")
        self.assertEqual(_keyword_score(results, keywords), 10.0)

    def test_partial_hits(self):
        results = [_hit(content="nearby metro station")]
        self.assertEqual(_keyword_score(results, ("metro", "airport", "railway")), 6.0)


class TestCountKeywordHits(unittest.TestCase):
    """Risk keywords must be asserted by the source, not merely present."""

    def test_counts_a_plainly_stated_signal(self):
        self.assertEqual(
            _count_keyword_hits([_hit("", "Market slowdown and oversupply of inventory")],
                                MARKET_SLOWDOWN_KEYWORDS), 2)
        self.assertEqual(
            _count_keyword_hits([_hit("", "Occasional flooding near the lake")],
                                FLOOD_KEYWORDS), 1)

    def test_a_negated_signal_is_not_a_signal(self):
        # Reported "locality has reported flooding" for coverage saying the
        # opposite, because the keyword was merely present in the text.
        for text in ("Steady absorption, no oversupply reported",
                     "No slowdown in this market"):
            self.assertEqual(
                _count_keyword_hits([_hit("", text)], MARKET_SLOWDOWN_KEYWORDS), 0, text)
        self.assertEqual(
            _count_keyword_hits([_hit("", "No flooding history in this locality")],
                                FLOOD_KEYWORDS), 0)

    def test_partial_words_still_match_but_not_across_a_word_start(self):
        # "waterlog" must still catch "waterlogging" ...
        self.assertEqual(
            _count_keyword_hits([_hit("", "Waterlogging during monsoon")], FLOOD_KEYWORDS), 2)
        # ... while "case" must not fire on "showcase".
        self.assertEqual(
            _count_keyword_hits([_hit("", "Builder showcase event")],
                                NEGATIVE_BUILDER_KEYWORDS), 0)


class TestClassifyGrowth(unittest.TestCase):
    def test_high_keyword(self):
        self.assertEqual(_classify_growth([_hit("New metro line announced")]), "High")

    def test_medium_keyword_without_high(self):
        self.assertEqual(_classify_growth([_hit("Upcoming park planned nearby")]), "Medium")

    def test_low_when_nothing_matches(self):
        self.assertEqual(_classify_growth([_hit("Local bakery opens")]), "Low")


class TestExtractPricesPerSqft(unittest.TestCase):
    def test_extracts_inr_and_rupee_forms(self):
        results = [
            _hit("Listing", "Average Rs. 8,500 / sqft in the area"),
            _hit("", "Going rate ₹9200 per sqft near the lake"),
            _hit("", "INR 7,200/sq.ft reported last quarter"),
        ]
        prices = _extract_prices_per_sqft(results)
        self.assertEqual(prices, [8500.0, 9200.0, 7200.0])

    def test_ignores_non_matching_text(self):
        self.assertEqual(_extract_prices_per_sqft([_hit("no prices here")]), [])


class TestExtractRentalYield(unittest.TestCase):
    def test_finds_percent_yield(self):
        results = [_hit("", "Area averages 3.5% rental yield across recent listings")]
        self.assertEqual(_extract_rental_yield(results), 3.5)

    def test_returns_none_when_absent(self):
        self.assertIsNone(_extract_rental_yield([_hit("rent is high")]))


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


class TestGroqCostMicros(unittest.TestCase):
    def test_zero_tokens_is_zero(self):
        self.assertEqual(groq_cost_micros(0, 0), 0)

    def test_one_million_input_tokens(self):
        # 1M input @ $0.59/MTok → $0.59 → 590_000 micros
        self.assertEqual(groq_cost_micros(1_000_000, 0), 590_000)

    def test_mixed_input_and_output(self):
        # 259 in + 88 out matches a typical recommendation call shape
        expected = round(((259 / 1_000_000) * 0.59 + (88 / 1_000_000) * 0.79) * 1_000_000)
        self.assertEqual(groq_cost_micros(259, 88), expected)


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
        self.assertAlmostEqual(budget.remaining(), 30.0, delta=0.5)

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
            self.assertAlmostEqual(budget.for_call(3), 9.5, places=2)
            self.assertAlmostEqual(budget.for_call(2), 12.0, places=2)  # capped
            self.assertAlmostEqual(budget.for_call(1), 12.0, places=2)
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
