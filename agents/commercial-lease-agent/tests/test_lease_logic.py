"""Direct tests for lease_logic — imports the module the way any Python code would.

Network-touching behavior is tested by substituting a fake `anthropic`
module in sys.modules before calling extract_clauses. Nothing here makes a
real HTTP request, even though a fake API key is set — the real anthropic
client is never constructed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lease_logic

# ---- pure function tests (no mocking needed) ----

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
        "page_number": 5,
        "confidence_score": 1.4,
    }
    cleaned = lease_logic._clean_clause(raw, pages)
    assert cleaned["page_number"] == 2
    assert cleaned["confidence_score"] == 1.0


def test_chunk_ranges_splits_long_document():
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
    assert lease_logic._estimate_cost_micros("claude-sonnet-5", 1_000_000, 1_000_000) == 12_000_000


def test_estimate_cost_micros_unknown_model_is_zero_not_fabricated():
    assert lease_logic._estimate_cost_micros("some-future-model-nobody-priced-yet", 1000, 1000) == 0


def test_is_well_formed_rejects_invalid_clause_type():
    bad = {"clause_type": "assignment", "text_quote": "x", "page_number": 1, "confidence_score": 0.5}
    assert lease_logic._is_well_formed(bad) is False


def test_is_well_formed_rejects_empty_quote():
    bad = {"clause_type": "renewal", "text_quote": "   ", "page_number": 1, "confidence_score": 0.5}
    assert lease_logic._is_well_formed(bad) is False


def test_is_well_formed_accepts_valid_clause():
    good = {"clause_type": "renewal", "text_quote": "x", "page_number": 1, "confidence_score": 0.5}
    assert lease_logic._is_well_formed(good) is True


# ---- extract_clauses tests, network fully substituted ----

class _FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeToolUseBlock:
    type = "tool_use"
    name = "record_clauses"

    def __init__(self, clauses):
        self.input = {"clauses": clauses}


class _FakeResponse:
    def __init__(self, clauses, input_tokens=100, output_tokens=50):
        self.content = [_FakeToolUseBlock(clauses)]
        self.usage = _FakeUsage(input_tokens, output_tokens)


class _FakeMessages:
    def __init__(self, queue):
        self._queue = list(queue)

    def create(self, **kwargs):
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeClient:
    def __init__(self, queue):
        self.messages = _FakeMessages(queue)


class _FakeAnthropicModule:
    def __init__(self, queue):
        self._queue = queue

    def Anthropic(self, api_key):
        return _FakeClient(self._queue)



def test_extract_clauses_success_across_two_chunks(monkeypatch):
    good_clause = {
        "clause_type": "renewal",
        "text_quote": "renewal clause text",
        "page_number": 1,
        "confidence_score": 0.9,
    }
    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        _FakeAnthropicModule([
            _FakeResponse([good_clause], input_tokens=100, output_tokens=40),
            _FakeResponse([], input_tokens=80, output_tokens=10),
        ]),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-testing-only")
    monkeypatch.setenv("LEASE_AGENT_CHUNK_SIZE", "1")

    pages = ["page one text with renewal clause text in it", "page two text"]
    clauses, usage = lease_logic.extract_clauses(pages)

    assert len(clauses) == 1
    assert clauses[0]["page_number"] == 1
    assert usage["input_tokens"] == 180
    assert usage["output_tokens"] == 50
    assert usage["cost_micros"] > 0


def test_extract_clauses_usage_survives_a_late_failure(monkeypatch):
    good_clause = {
        "clause_type": "renewal",
        "text_quote": "renewal clause text",
        "page_number": 1,
        "confidence_score": 0.9,
    }
    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        _FakeAnthropicModule([
            _FakeResponse([good_clause], input_tokens=100, output_tokens=40),
            RuntimeError("simulated network drop on the second chunk"),
        ]),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-testing-only")
    monkeypatch.setenv("LEASE_AGENT_CHUNK_SIZE", "1")

    pages = ["page one text with renewal clause text in it", "page two text"]
    try:
        lease_logic.extract_clauses(pages)
    except lease_logic.ModelCallError as exc:
        # the first chunk's tokens were genuinely billed before the second chunk failed
        assert exc.usage["input_tokens"] == 100
        assert exc.usage["output_tokens"] == 40
    else:
        raise AssertionError("expected ModelCallError")


def test_extract_clauses_rejects_unusable_model_output(monkeypatch):
    bad_clause = {"clause_type": "not-a-real-type", "text_quote": "x", "page_number": 1, "confidence_score": 0.5}
    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        _FakeAnthropicModule([_FakeResponse([bad_clause], input_tokens=50, output_tokens=20)]),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-testing-only")

    try:
        lease_logic.extract_clauses(["some page text"])
    except lease_logic.ModelCallError as exc:
        assert exc.usage["input_tokens"] == 50  # billed even though the output was unusable
    else:
        raise AssertionError("expected ModelCallError for unusable model output")
