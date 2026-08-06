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