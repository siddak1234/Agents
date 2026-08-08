"""Direct tests for lease_logic — imports the module the way any Python code would.

Network-touching behavior is tested by substituting a fake `anthropic`
module in sys.modules before calling extract_clauses. Nothing here makes a
real HTTP request, even though a fake API key is set — the real anthropic
client is never constructed.
"""

import json
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
    assert lease_logic._locate_page("renewal clause", pages) == 2


def test_locate_page_returns_none_when_quote_is_on_no_page():
    pages = ["first page", "second page"]
    assert lease_logic._locate_page("not present anywhere", pages) is None


def test_locate_page_tolerates_pdf_line_wrapping():
    # Page text out of a PDF wraps where the layout did; a genuinely verbatim
    # quote still would not match byte-for-byte.
    pages = ["Tenant shall maintain\nthe Premises in good\ncondition."]
    assert lease_logic._locate_page("Tenant shall maintain the Premises", pages) == 1


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


def test_clean_clause_drops_a_quote_found_on_no_page():
    # The promise in agent.yaml is a verbatim quote and the page it came from.
    # A paraphrase honours neither, and the model's claimed page is not
    # evidence — so it must not come back looking like a checked citation.
    pages = ["Tenant shall pay Base Rent of $10.00 per rentable square foot."]
    raw = {
        "clause_type": "financial",
        "text_quote": "Tenant shall pay Base Rent of $10.00 per square footage.",
        "page_number": 2,
        "confidence_score": 0.95,
    }
    assert lease_logic._clean_clause(raw, pages) is None


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


def test_usage_names_the_model_and_never_money():
    # docs/AGENT_PROTOCOL.md: usage carries tokens and the model, not a price.
    # An agent still emitting cost_micros decodes as model: null, silently
    # claiming it called nothing.
    usage = lease_logic._usage_dict("claude-sonnet-5", 412, 96)
    assert usage == {"input_tokens": 412, "output_tokens": 96, "model": "claude-sonnet-5"}
    assert "cost_micros" not in usage


def test_calculate_deadline_rejects_a_unit_mix_up_as_a_caller_error():
    # 90 days expressed in seconds. Uncaught this is an OverflowError, which
    # escapes as `internal` and tells the caller nothing actionable.
    try:
        lease_logic.calculate_deadline("2026-01-01", 90 * 24 * 60 * 60)
    except lease_logic.LeaseLogicError as exc:
        assert "notice_period_days" in str(exc)
    else:
        raise AssertionError("expected LeaseLogicError for an out-of-range notice period")


def test_is_transient_separates_configuration_from_a_blip():
    # docs/INTERN_BRIEF.md's error table: `unavailable` is retryable only when
    # the cause is transient. A rejected key is configuration, and retrying it
    # just re-spends money on the identical rejection.
    class _StatusError(Exception):
        def __init__(self, code):
            self.status_code = code

    assert lease_logic._is_transient(_StatusError(401)) is False
    assert lease_logic._is_transient(_StatusError(403)) is False
    assert lease_logic._is_transient(_StatusError(404)) is False
    assert lease_logic._is_transient(_StatusError(429)) is True
    assert lease_logic._is_transient(_StatusError(503)) is True
    assert lease_logic._is_transient(ConnectionError("dropped")) is True


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

    def Anthropic(self, api_key):  # noqa: N802 — must match anthropic.Anthropic's real name
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
    assert usage["model"] == lease_logic.DEFAULT_MODEL
    assert "cost_micros" not in usage


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


def test_extract_clauses_marks_a_rejected_key_non_retryable(monkeypatch):
    class _AuthError(Exception):
        status_code = 401

    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        _FakeAnthropicModule([_AuthError("invalid x-api-key")]),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-testing-only")

    try:
        lease_logic.extract_clauses(["some page text"])
    except lease_logic.ModelCallError as exc:
        assert exc.retryable is False, "a rejected key is configuration, not a blip"
    else:
        raise AssertionError("expected ModelCallError")


def test_extract_clauses_marks_a_rate_limit_retryable(monkeypatch):
    class _RateLimitedError(Exception):
        status_code = 429

    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        _FakeAnthropicModule([_RateLimitedError("slow down")]),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-testing-only")

    try:
        lease_logic.extract_clauses(["some page text"])
    except lease_logic.ModelCallError as exc:
        assert exc.retryable is True
    else:
        raise AssertionError("expected ModelCallError")


def test_extract_clauses_drops_an_invented_quote_but_keeps_a_real_one(monkeypatch):
    real = {
        "clause_type": "renewal",
        "text_quote": "Tenant may renew for one additional term of five years.",
        "page_number": 1,
        "confidence_score": 0.9,
    }
    invented = {
        "clause_type": "financial",
        "text_quote": "Rent increases by twelve percent annually.",  # on no page
        "page_number": 1,
        "confidence_score": 0.99,
    }
    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        _FakeAnthropicModule([_FakeResponse([real, invented])]),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-testing-only")

    clauses, _ = lease_logic.extract_clauses(
        ["Tenant may renew for one additional term of five years."]
    )
    assert [c["text_quote"] for c in clauses] == [real["text_quote"]]


def test_tool_schema_matches_the_golden_fixture():
    # Changing the schema the model is held to should be a deliberate act, as
    # it is for realty-lead-gen and investment-due-diligence.
    golden = json.loads((Path(__file__).parent / "golden" / "record_clauses_tool_schema.json").read_text())
    assert lease_logic._build_tool_schema() == golden
