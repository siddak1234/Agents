"""Direct tests for lease_logic — imports the module the way any Python code would."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lease_logic


def test_calculate_deadline_basic():
    assert lease_logic.calculate_deadline("2028-12-31", 90) == "2028-10-02"


def test_calculate_deadline_zero_notice():
    assert lease_logic.calculate_deadline("2028-01-15", 0) == "2028-01-15"


def test_calculate_deadline_rejects_bad_format():
    try:
        lease_logic.calculate_deadline("31-12-2028", 90)
    except lease_logic.LeaseLogicError:
        pass
    else:
        raise AssertionError("expected LeaseLogicError for a non-ISO date")


def test_locate_page_finds_exact_quote():
    pages = ["first page text", "the renewal clause is here", "third page"]
    assert lease_logic._locate_page("renewal clause", pages, fallback=1) == 2


def test_locate_page_falls_back_when_quote_not_found():
    pages = ["first page", "second page"]
    assert lease_logic._locate_page("not present anywhere", pages, fallback=1) == 1


def test_clean_clause_recomputes_page_and_clamps_confidence():
    pages = ["nothing here", "the maintenance clause text"]
    raw = {
        "clause_type": "maintenance",
        "text_quote": "the maintenance clause text",
        "page_number": 5,          # deliberately wrong, to prove it gets corrected
        "confidence_score": 1.4,   # deliberately out of range
    }
    cleaned = lease_logic._clean_clause(raw, pages)
    assert cleaned["page_number"] == 2
    assert cleaned["confidence_score"] == 1.0


def test_chunk_ranges_splits_long_document():
    # a 250-page lease at the default 20-page chunk size -> 13 chunks, last one partial
    ranges = lease_logic._chunk_ranges(250, 20)
    assert len(ranges) == 13
    assert ranges[0] == (0, 20)
    assert ranges[-1] == (240, 250)


def test_chunk_ranges_short_document_is_one_chunk():
    assert lease_logic._chunk_ranges(5, 20) == [(0, 5)]


def test_positive_int_falls_back_on_invalid_or_missing():
    assert lease_logic._positive_int(None, 20) == 20
    assert lease_logic._positive_int("not-a-number", 20) == 20
    assert lease_logic._positive_int("-5", 20) == 20
    assert lease_logic._positive_int("7", 20) == 7


def test_estimate_cost_micros_known_model():
    # 1,000,000 input tokens @ $2/MTok + 1,000,000 output tokens @ $10/MTok = $12
    assert lease_logic._estimate_cost_micros("claude-sonnet-5", 1_000_000, 1_000_000) == 12_000_000


def test_estimate_cost_micros_unknown_model_is_zero_not_fabricated():
    assert lease_logic._estimate_cost_micros("some-future-model-nobody-priced-yet", 1000, 1000) == 0