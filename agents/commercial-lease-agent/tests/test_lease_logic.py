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


def test_locate_page_prefers_the_page_the_model_claimed():
    """Leases repeat clauses in a summary sheet; first-match would cite that."""
    pages = ["SUMMARY: Base Rent $42.00 per foot.", "definitions", "Base Rent $42.00 per foot."]
    assert lease_logic._locate_page("Base Rent $42.00 per foot.", pages, claimed=3) == 3
    # A claim that does not check out falls back to searching.
    assert lease_logic._locate_page("Base Rent $42.00 per foot.", pages, claimed=2) == 1


def test_normalise_folds_what_pdf_extraction_differs_on():
    # ruff: noqa: RUF001 — the ambiguous characters are the subject of the test
    page = ("Landlord shall main-\ntain the “Premises” at Landlord’s expense — "
            "including the ﬁrst full Lease Year.")
    quote = 'Landlord shall maintain the "Premises" at Landlord\'s expense - including the first full Lease Year.'
    assert lease_logic._locate_page(quote, [page]) == 1


def test_normalise_still_rejects_what_actually_differs():
    page = "Landlord shall repair the roof at a cost of $42.00."
    for wrong in ("Tenant shall repair the roof at a cost of $42.00.",
                  "Landlord shall not repair the roof at a cost of $42.00.",
                  "Landlord shall repair the roof at a cost of $52.00."):
        assert lease_logic._locate_page(wrong, [page]) is None, wrong


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


def test_clean_clause_marks_an_unlocatable_quote_unverified_not_gone():
    # A paraphrase must not look like a checked citation — but deleting it made
    # "no such clause" and "quote not verifiable" the same answer.
    pages = ["Tenant shall pay Base Rent of $10.00 per rentable square foot."]
    raw = {
        "clause_type": "financial",
        "text_quote": "Tenant shall pay Base Rent of $10.00 per square footage.",
        "page_number": 2,
        "confidence_score": 0.95,
    }
    out = lease_logic._clean_clause(raw, pages)
    assert out["page_number"] is None
    assert out["claimed_page_number"] == 2
    assert out["text_quote"] == raw["text_quote"]


def test_clean_clause_offsets_the_page_for_a_split_document():
    pages = ["Base Rent is $42.00 per foot."]
    raw = {"clause_type": "financial", "text_quote": "Base Rent is $42.00 per foot.",
           "page_number": 1, "confidence_score": 0.9}
    assert lease_logic._clean_clause(raw, pages, first_page_number=1001)["page_number"] == 1001


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
    def __init__(self, clauses, input_tokens=100, output_tokens=50, stop_reason="end_turn"):
        self.content = [_FakeToolUseBlock(clauses)]
        self.usage = _FakeUsage(input_tokens, output_tokens)
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, queue):
        self._queue = list(queue)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
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
        self.last_client: _FakeClient | None = None

    def Anthropic(self, api_key):  # noqa: N802 — must match anthropic.Anthropic's real name
        self.last_client = _FakeClient(self._queue)
        return self.last_client



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
    clauses, _unverified, usage = lease_logic.extract_clauses(pages)

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


def test_extract_clauses_reports_an_invented_quote_without_losing_a_real_one(monkeypatch):
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

    clauses, unverified, _ = lease_logic.extract_clauses(
        ["Tenant may renew for one additional term of five years."]
    )
    assert [c["text_quote"] for c in clauses] == [real["text_quote"]]
    # The invented one is reported, not silently gone.
    assert [c["text_quote"] for c in unverified] == [invented["text_quote"]]
    assert unverified[0]["page_number"] is None


def test_tool_schema_matches_the_golden_fixture():
    # Changing the schema the model is held to should be a deliberate act, as
    # it is for realty-lead-gen and investment-due-diligence.
    golden = json.loads((Path(__file__).parent / "golden" / "record_clauses_tool_schema.json").read_text())
    assert lease_logic._build_tool_schema() == golden


def test_one_chunks_miss_does_not_destroy_the_other_chunks(monkeypatch):
    """The blocker this change exists for.

    A quote that failed to match used to raise, discarding every clause already
    extracted and verified from earlier chunks — twelve good chunks of a
    250-page lease binned because of the thirteenth, fully billed, with
    retryable:false telling the caller to give up for good.
    """
    def cl(t, q, p):
        return {"clause_type": t, "text_quote": q, "page_number": p, "confidence_score": 0.9}

    pages = [
        "Tenant may renew for five years.",
        "Base Rent is $42.00 per foot.",
        "Landlord maintains all structural elements.",
    ]
    monkeypatch.setitem(sys.modules, "anthropic", _FakeAnthropicModule([
        _FakeResponse([cl("renewal", pages[0], 1)]),
        _FakeResponse([cl("financial", pages[1], 2)]),
        # The model paraphrases on the last chunk — nothing locatable.
        _FakeResponse([cl("maintenance", "Landlord looks after the structure.", 3)]),
    ]))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-testing-only")
    monkeypatch.setenv("LEASE_AGENT_CHUNK_SIZE", "1")

    clauses, unverified, usage = lease_logic.extract_clauses(pages)

    assert [c["page_number"] for c in clauses] == [1, 2]
    assert len(unverified) == 1
    assert usage["input_tokens"] == 300  # all three chunks billed, all three reported


def test_messages_create_never_sends_temperature(monkeypatch):
    """Sonnet 5, like Opus 5 and every 4.6+ model, rejects a non-default
    sampling parameter with a 400 — the API default is 1.0, so
    temperature=0.0 fails on every call. This was shipped and reached a
    review board green before anyone ran it against the real API.
    """
    good_clause = {
        "clause_type": "renewal",
        "text_quote": "renewal clause text",
        "page_number": 1,
        "confidence_score": 0.9,
    }
    fake_module = _FakeAnthropicModule([
        _FakeResponse([good_clause], input_tokens=100, output_tokens=40),
    ])
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-testing-only")

    lease_logic.extract_clauses(["page one text with renewal clause text in it"])

    assert fake_module.last_client is not None
    calls = fake_module.last_client.messages.calls
    assert len(calls) == 1
    assert "temperature" not in calls[0], (
        "temperature must never be sent — claude-sonnet-5 rejects a "
        "non-default value with a 400 on every call"
    )


def test_extract_clauses_rejects_a_truncated_tool_call(monkeypatch):
    """stop_reason=max_tokens means output was cut off — the clauses that made
    it through can still be syntactically valid, and nothing distinguishes
    "the chunk held only these" from "there were more, never seen".
    """
    good_clause = {
        "clause_type": "renewal",
        "text_quote": "Tenant may renew for five years.",
        "page_number": 1,
        "confidence_score": 0.9,
    }
    monkeypatch.setitem(sys.modules, "anthropic", _FakeAnthropicModule([
        _FakeResponse([good_clause], input_tokens=4000, output_tokens=4096,
                       stop_reason="max_tokens"),
    ]))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-testing-only")

    try:
        lease_logic.extract_clauses(["Tenant may renew for five years."])
    except lease_logic.ModelCallError as exc:
        assert "truncated" in str(exc)
        assert exc.usage["input_tokens"] == 4000  # billed even though rejected
    else:
        raise AssertionError("expected ModelCallError for a max_tokens-truncated response")


def test_extract_clauses_accepts_a_normal_completion(monkeypatch):
    """The default stop_reason must not itself trigger the truncation guard."""
    good_clause = {
        "clause_type": "renewal",
        "text_quote": "Tenant may renew for five years.",
        "page_number": 1,
        "confidence_score": 0.9,
    }
    monkeypatch.setitem(sys.modules, "anthropic", _FakeAnthropicModule([
        _FakeResponse([good_clause], stop_reason="end_turn"),
    ]))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-testing-only")

    clauses, _unverified, _usage = lease_logic.extract_clauses(["Tenant may renew for five years."])
    assert len(clauses) == 1


def test_locate_page_does_not_match_across_a_column_gutter():
    """A rent table's numbers, lined up under a header with wide gaps, are
    not contiguous prose. Matching across that gap let a quote verify
    against text that was never actually adjacent on the page.
    """
    page = "Base Rent          $42.00\nCAM Charges        $ 8.00"
    assert lease_logic._locate_page("Base Rent $42.00 CAM Charges", [page]) is None


def test_locate_page_tolerates_two_spaces_after_a_period():
    """The gutter guard triggers at three-or-more spaces, not two — the
    common typewriter convention of two spaces after a sentence must still
    match normally.
    """
    page = "Tenant shall vacate the Premises.  Landlord may re-let immediately."
    assert lease_logic._locate_page(
        "Tenant shall vacate the Premises. Landlord may re-let immediately.", [page]
    ) == 1


def test_extract_clauses_dedupes_a_clause_restated_across_chunks(monkeypatch):
    """The extraction prompt tells the model to report a clause once per
    chunk; it cannot see other chunks, so a summary-then-body restatement
    split across a chunk boundary reached the caller twice.
    """
    def cl(clause_type, quote, page, confidence):
        return {"clause_type": clause_type, "text_quote": quote,
                "page_number": page, "confidence_score": confidence}

    quote = "Tenant may renew for five years upon notice."
    monkeypatch.setitem(sys.modules, "anthropic", _FakeAnthropicModule([
        _FakeResponse([cl("renewal", quote, 1, 0.7)]),
        _FakeResponse([cl("renewal", quote, 2, 0.95)]),
    ]))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-testing-only")
    monkeypatch.setenv("LEASE_AGENT_CHUNK_SIZE", "1")

    clauses, _unverified, _usage = lease_logic.extract_clauses([quote, quote])

    assert len(clauses) == 1
    assert clauses[0]["confidence_score"] == 0.95  # the higher-confidence occurrence survives
    assert clauses[0]["page_number"] == 2


def test_extract_clauses_keeps_the_first_occurrence_on_a_confidence_tie(monkeypatch):
    def cl(clause_type, quote, page, confidence):
        return {"clause_type": clause_type, "text_quote": quote,
                "page_number": page, "confidence_score": confidence}

    quote = "Tenant shall pay Base Rent of $42.00 per foot."
    monkeypatch.setitem(sys.modules, "anthropic", _FakeAnthropicModule([
        _FakeResponse([cl("financial", quote, 1, 0.9)]),
        _FakeResponse([]),
        _FakeResponse([]),
        _FakeResponse([]),
        _FakeResponse([cl("financial", quote, 5, 0.9)]),
    ]))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-testing-only")
    monkeypatch.setenv("LEASE_AGENT_CHUNK_SIZE", "1")

    clauses, _unverified, _usage = lease_logic.extract_clauses([quote] + ["filler"] * 3 + [quote])

    assert len(clauses) == 1
    assert clauses[0]["page_number"] == 1  # earlier occurrence wins a tie


def test_extract_clauses_does_not_merge_different_clause_types(monkeypatch):
    """Same wording, different obligation types — must not collapse into one."""
    def cl(clause_type, quote, page, confidence):
        return {"clause_type": clause_type, "text_quote": quote,
                "page_number": page, "confidence_score": confidence}

    quote = "Tenant shall maintain the equipment in good condition."
    monkeypatch.setitem(sys.modules, "anthropic", _FakeAnthropicModule([
        _FakeResponse([cl("maintenance", quote, 1, 0.9)]),
        _FakeResponse([cl("financial", quote, 2, 0.9)]),
    ]))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-testing-only")
    monkeypatch.setenv("LEASE_AGENT_CHUNK_SIZE", "1")

    clauses, _unverified, _usage = lease_logic.extract_clauses([quote, quote])

    assert len(clauses) == 2


def test_dedupe_clauses_is_a_pure_function():
    def cl(clause_type, quote, page, confidence):
        return {"clause_type": clause_type, "text_quote": quote,
                "page_number": page, "confidence_score": confidence}

    out = lease_logic._dedupe_clauses([
        cl("renewal", "same text", 1, 0.6),
        cl("renewal", "same text", 3, 0.6),
        cl("renewal", "different text entirely", 2, 0.5),
    ])
    assert len(out) == 2
    assert out[0]["page_number"] == 1  # tie kept the first
    assert out[1]["text_quote"] == "different text entirely"
