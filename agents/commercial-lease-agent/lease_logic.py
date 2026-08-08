"""Business logic for the commercial-lease-agent.

Two independent pieces of work live here:
  - calculate_deadline: pure date arithmetic, no external dependency.
  - extract_clauses: reads lease pages in bounded chunks, calls an LLM per
    chunk using multi-shot prompting and a tool schema, validates the
    model's output against the types agent.yaml declares, checks every
    quote against the page it claims to come from, and merges results
    across chunks.

Nothing here knows about the agentcall protocol. Every function takes and
returns plain Python values, so this file can be tested by itself.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import sys
import unicodedata
from typing import Any

VALID_CLAUSE_TYPES = ("renewal", "financial", "maintenance")

# claude-sonnet-5 is the correct, current API model ID (verified against
# platform.claude.com/docs). Override without touching code via LEASE_AGENT_MODEL.
DEFAULT_MODEL = "claude-sonnet-5"

# Pages sent to the model per call. Keeps each call's output size bounded
# and reliable regardless of total lease length. Override via
# LEASE_AGENT_CHUNK_SIZE.
DEFAULT_CHUNK_SIZE = 20

_MAX_OUTPUT_TOKENS_PER_CHUNK = 4096

# What one request may spend. A page count alone bounds neither calls nor
# tokens — LEASE_AGENT_CHUNK_SIZE moves 1000 pages between 50 calls and 1000,
# and 999 pages of half a megabyte each pass a page cap untouched while being
# the thing that actually costs money. So the characters are capped too, and
# generously: 4,000,000 is roughly 1M tokens, far past any real lease.
MAX_LEASE_PAGES = 1000
MAX_LEASE_CHARS = 4_000_000

_EXTRACTION_GUIDANCE = """\
You are extracting clauses from a commercial lease excerpt. Work only from
the text given below — do not use outside knowledge about typical leases.

WHAT TO EXTRACT
- renewal: automatic renewal, renewal options, non-renewal notice requirements.
- financial: base rent, escalations, percentage rent, security deposit, CAM charges.
- maintenance: who repairs what (structural, roof, HVAC, interior, common areas).

WHAT NOT TO EXTRACT
- Definitions sections, recitals, signature blocks, notary acknowledgments.
- Clauses about insurance, indemnification, assignment, or default that do not
  also state a renewal, financial, or maintenance obligation.
- Headers, footers, and page or section numbers on their own.

RULES
- text_quote must be copied character-for-character from the page text below
  — never paraphrase or summarize it.
- If the same clause appears more than once (e.g. once in a table of
  contents or summary, again in the body), extract it once, from the most
  complete instance.
- confidence_score reflects how clearly the text states the obligation: use
  0.9-1.0 for an explicit, unambiguous statement; 0.6-0.8 when the wording
  is present but needs some interpretation; below 0.6 only for a clause
  you're genuinely unsure qualifies. Extract ambiguous clauses rather than
  omitting them — let the low score carry the uncertainty.

EXAMPLES

Excerpt: "Tenant may renew this Lease for one additional term of five (5)
years by delivering written notice to Landlord not less than one hundred
eighty (180) days prior to the expiration of the then-current term."
Good extraction: clause_type "renewal", text_quote is that exact sentence,
confidence_score 0.97 — the renewal right and notice period are both stated
explicitly with no ambiguity.

Excerpt: "Base Rent for the initial Lease Year shall be $42.00 per rentable
square foot, increasing by three percent (3%) on each anniversary of the
Commencement Date."
Good extraction: clause_type "financial", text_quote is that exact
sentence, confidence_score 0.96 — a concrete rent figure and escalation
rate are both stated.

Excerpt: "Tenant shall maintain the Premises in good condition, reasonable
wear and tear excepted, and shall be responsible for repairs arising from
Tenant's negligence; Landlord shall be responsible for the structural
elements of the Building, including the roof, foundation, and load-bearing
walls."
Good extraction: two separate maintenance entries, one for Tenant's
interior/negligence-repair obligation and one for Landlord's structural
obligation, each with its own exact text_quote — they assign responsibility
to different parties, so a caller needs to tell them apart.
"""


class LeaseLogicError(Exception):
    """Base class for business-level failures a caller should see as an error."""


class MissingCredentialError(LeaseLogicError):
    """Raised when a required credential is not configured. Nothing was
    billed yet when this is raised."""

class MissingDependencyError(LeaseLogicError):
    """Raised when a required third-party package is not installed."""

class ModelRequestError(LeaseLogicError):
    """Raised when the API rejects the request itself — almost always an
    excerpt too large for one call. The caller's input is at fault, not a
    dependency, so this maps to `invalid_request`."""

class ModelCallError(LeaseLogicError):
    """Raised when a model call fails, or returns output that doesn't pass
    validation. Carries usage accumulated before the failure, since earlier
    chunk calls in the same request may already have been billed even though
    this one failed, and whether trying again could possibly help.
    """

    def __init__(
        self,
        message: str,
        usage: dict[str, Any] | None = None,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.usage = usage or {"input_tokens": 0, "output_tokens": 0, "model": None}
        self.retryable = retryable


# Which failures are worth another attempt. Mirrors realty-lead-gen's
# utils/http.py, so "does a retry help" is one answer across the repository
# rather than a per-agent opinion: connection-level trouble and server-side
# or rate-limit statuses are transient; anything the request itself got wrong
# — a rejected key, a model name that does not exist — is not, and retrying
# it only re-spends money on the identical rejection.
_TRANSIENT_STATUS = frozenset({408, 409, 429})
_BAD_REQUEST_STATUS = frozenset({400, 413, 422})
_SERVER_ERROR = 500


def _is_transient(exc: Exception, anthropic: Any = None) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in _TRANSIENT_STATUS or status >= _SERVER_ERROR
    # `APITimeoutError` subclasses `APIConnectionError`, so one isinstance
    # covers both — and any future sibling the vendor adds. Matching on the
    # class *name* silently stopped working the moment either was renamed.
    connection_error = getattr(anthropic, "APIConnectionError", None)
    if isinstance(connection_error, type) and isinstance(exc, connection_error):
        return True
    return isinstance(exc, ConnectionError | TimeoutError)


def _is_bad_request(exc: Exception) -> bool:
    """The API refused the request itself, rather than failing to serve it."""
    return getattr(exc, "status_code", None) in _BAD_REQUEST_STATUS


def calculate_deadline(lease_end_date: str, notice_period_days: int) -> str:
    """Return the date that is `notice_period_days` days before `lease_end_date`."""
    try:
        end = _dt.date.fromisoformat(lease_end_date)
    except ValueError as exc:
        raise LeaseLogicError(f"'lease_end_date' must be a real YYYY-MM-DD date: {exc}") from None
    try:
        deadline = end - _dt.timedelta(days=notice_period_days)
    except OverflowError:
        # The caller's mistake, not ours — most often a unit mix-up, passing
        # the notice period in seconds or milliseconds. Left uncaught this
        # escapes as `internal`, which tells the caller nothing actionable.
        raise LeaseLogicError(
            f"'notice_period_days' ({notice_period_days}) reaches before year 1 "
            f"from 'lease_end_date' {lease_end_date} — check the unit is days"
        ) from None
    return deadline.isoformat()


def extract_clauses(
    lease_pages: list[str], first_page_number: int = 1
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Extract renewal/financial/maintenance clauses using an LLM, chunked.

    Returns (clauses, unverified_clauses, usage). A clause whose quote was
    found in the pages supplied carries the page it was found on; one that was
    not comes back in `unverified_clauses` with `page_number: None` and the
    model's own claim kept separately. Neither list is silently discarded —
    a caller has to be able to tell "the lease says nothing about renewal"
    from "the model paraphrased the renewal clause".

    `first_page_number` is added to every located page, so a caller splitting
    a long document reports real page numbers rather than each part's offsets.

    Callers are expected to have bounded `lease_pages` already; agent_main does
    it, alongside its other input validation.

    Raises MissingDependencyError if the anthropic package isn't installed,
    MissingCredentialError if the API key is not configured (neither bills
    anything), ModelRequestError if the API rejected the request as malformed,
    or ModelCallError if a call fails or returns unusable output (usage
    attached, reflecting anything already billed in this request).
    """
    try:
        import anthropic  # noqa: PLC0415 — deliberately deferred so describe/
        # calculate_deadline keep working even if this dependency is missing
    except ImportError as exc:
        raise MissingDependencyError(f"the 'anthropic' package is not installed: {exc}") from exc

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise MissingCredentialError("ANTHROPIC_API_KEY is not set")
    model = os.environ.get("LEASE_AGENT_MODEL", DEFAULT_MODEL)
    chunk_size = _positive_int(os.environ.get("LEASE_AGENT_CHUNK_SIZE"), DEFAULT_CHUNK_SIZE)

    client = anthropic.Anthropic(api_key=api_key)
    tool = _build_tool_schema()

    all_clauses: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    input_tokens = 0
    output_tokens = 0

    for start, end in _chunk_ranges(len(lease_pages), chunk_size):
        chunk = lease_pages[start:end]
        prompt = _build_prompt(chunk, start)

        try:
            response = client.messages.create(
                model=model,
                max_tokens=_MAX_OUTPUT_TOKENS_PER_CHUNK,
                temperature=0.0,
                tools=[tool],
                tool_choice={"type": "tool", "name": "record_clauses"},
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:  # network or API-side failure — nothing from this call was billed
            if _is_bad_request(exc):
                # The API refused the request itself — overwhelmingly an excerpt
                # too large for one call. That is the caller's input, not a
                # dependency being down, and `unavailable` would send them to
                # check our status page instead of their page sizes.
                raise ModelRequestError(
                    f"the model rejected the request for pages {start + 1}-{end}: {exc} "
                    "— reduce page sizes or lower LEASE_AGENT_CHUNK_SIZE"
                ) from exc
            raise ModelCallError(
                f"model call failed on pages {start + 1}-{end}: {exc}",
                usage=_usage_dict(model, input_tokens, output_tokens),
                retryable=_is_transient(exc, anthropic),
            ) from exc

        # This call's tokens are billed now, regardless of what happens next.
        input_tokens += getattr(response.usage, "input_tokens", 0)
        output_tokens += getattr(response.usage, "output_tokens", 0)

        # Anything the model hands back is suspect until checked, so a parse
        # failure here is still a billed call and must carry its usage out.
        try:
            raw_clauses = _extract_tool_input(response)
        except ModelCallError as exc:
            exc.usage = _usage_dict(model, input_tokens, output_tokens)
            raise
        except Exception as exc:
            raise ModelCallError(
                f"model returned an unreadable tool call on pages {start + 1}-{end}: {exc}",
                usage=_usage_dict(model, input_tokens, output_tokens),
            ) from exc

        well_formed = [c for c in raw_clauses if _is_well_formed(c)]
        if raw_clauses and not well_formed:
            # Nothing the model returned even matches the types we declared.
            # That is the model failing, not a bad request from our caller —
            # and unlike an unlocatable quote it leaves us nothing to report.
            raise ModelCallError(
                f"model returned {len(raw_clauses)} clause(s) on pages {start + 1}-{end}, "
                "none matching the types the tool schema declares",
                usage=_usage_dict(model, input_tokens, output_tokens),
            )
        if len(well_formed) < len(raw_clauses):
            print(
                f"lease_logic: discarded {len(raw_clauses) - len(well_formed)} malformed "
                f"clause(s) on pages {start + 1}-{end}",
                file=sys.stderr,
            )

        for c in well_formed:
            cleaned = _clean_clause(c, lease_pages, first_page_number)
            # A quote we could not find is reported, never thrown away, and
            # never allowed to sink the chunks that did verify.
            (all_clauses if cleaned["page_number"] is not None else unverified).append(cleaned)

    return all_clauses, unverified, _usage_dict(model, input_tokens, output_tokens)


def _chunk_ranges(total_pages: int, chunk_size: int) -> list[tuple[int, int]]:
    """Split `total_pages` into (start, end) index pairs, end exclusive."""
    return [(i, min(i + chunk_size, total_pages)) for i in range(0, total_pages, chunk_size)]


def _positive_int(value: str | None, default: int) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    else:
        return n if n > 0 else default

def _build_tool_schema() -> dict[str, Any]:
    return {
        "name": "record_clauses",
        "description": "Record every renewal, financial, or maintenance clause found in this excerpt.",
        "input_schema": {
            "type": "object",
            "required": ["clauses"],
            "additionalProperties": False,
            "properties": {
                "clauses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["clause_type", "text_quote", "page_number", "confidence_score"],
                        "additionalProperties": False,
                        "properties": {
                            "clause_type": {"type": "string", "enum": list(VALID_CLAUSE_TYPES)},
                            "text_quote": {"type": "string"},
                            "page_number": {"type": "integer"},
                            "confidence_score": {"type": "number"},
                        },
                    },
                }
            },
        },
    }


def _build_prompt(pages: list[str], offset: int) -> str:
    numbered = "\n\n".join(f"--- page {offset + i + 1} ---\n{text}" for i, text in enumerate(pages))
    return (
        _EXTRACTION_GUIDANCE
        + "\n\nNow extract from this excerpt "
        f"(pages {offset + 1}-{offset + len(pages)} of the full document). "
        "Call record_clauses with everything you find in this excerpt, and "
        "nothing else.\n\n" + numbered
    )


def _extract_tool_input(response: Any) -> list[dict[str, Any]]:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_clauses":
            return block.input.get("clauses", [])
    raise ModelCallError("model did not return a record_clauses tool call")


def _is_well_formed(clause: dict[str, Any]) -> bool:
    """Check clause against the *types* agent.yaml declares, not just key presence."""
    required = ("clause_type", "text_quote", "page_number", "confidence_score")
    if not isinstance(clause, dict) or not all(k in clause for k in required):
        return False
    if clause["clause_type"] not in VALID_CLAUSE_TYPES:
        return False
    if not isinstance(clause["text_quote"], str) or not clause["text_quote"].strip():
        return False
    try:
        int(clause["page_number"])
    except (TypeError, ValueError):
        return False
    try:
        float(clause["confidence_score"])
    except (TypeError, ValueError):
        return False
    return True


def _clean_clause(
    clause: dict[str, Any], lease_pages: list[str], first_page_number: int = 1
) -> dict[str, Any]:
    """Verify the quote against the source pages, or mark it unverified.

    agent.yaml promises `text_quote` is "copied verbatim from the source page"
    and `page_number` is where it was found. A quote found on no page honours
    neither, and the model's claimed page is not evidence that it does — so it
    comes back in `unverified_clauses` with `page_number: null` instead of
    being passed off as a checked citation.

    It is not dropped. Deleting it made "no renewal clause in this lease"
    indistinguishable from "the renewal clause was paraphrased", which for a
    legal document is the more dangerous of the two answers to guess at.
    """
    quote = str(clause["text_quote"])
    confidence = max(0.0, min(1.0, float(clause["confidence_score"])))
    claimed = int(clause["page_number"])
    page_number = _locate_page(quote, lease_pages, claimed)
    if page_number is None:
        return {
            "clause_type": clause["clause_type"],
            "text_quote": quote,
            "page_number": None,
            "claimed_page_number": claimed,
            "confidence_score": confidence,
        }
    return {
        "clause_type": clause["clause_type"],
        "text_quote": quote,
        "page_number": page_number + first_page_number - 1,
        "confidence_score": confidence,
    }


def _locate_page(quote: str, lease_pages: list[str], claimed: int | None = None) -> int | None:
    """The 1-indexed page whose text contains `quote`, or None if none does.

    The model's own claim is checked first. Leases repeat operative sentences —
    a Basic Lease Information cover sheet, a table of contents, an exhibit
    summary — so first-match search would rewrite a correct body-page citation
    to the summary page, which is the opposite of what verification is for. A
    claim that checks out is a verified citation and strictly more information
    than the lowest matching index.
    """
    needle = _normalise(quote)
    if not needle:
        return None
    if claimed is not None and 1 <= claimed <= len(lease_pages) and needle in _normalise(lease_pages[claimed - 1]):
        return claimed
    for i, page_text in enumerate(lease_pages):
        if needle in _normalise(page_text):
            return i + 1
    return None


def _normalise(text: str) -> str:
    """Fold away what PDF extraction and model transcription differ on.

    Exact substring matching after collapsing whitespace alone drops quotes
    that are genuinely verbatim: measured against realistic extracted text, 6
    of 12 correct quotes failed on hyphenation at a line break, typographic
    quotes and apostrophes, en/em dashes, ligatures and soft hyphens. All of
    those are ordinary in legal PDFs. Folding them costs nothing that matters —
    figures, negation and which party owes the obligation all still have to
    match exactly.
    """
    text = _LINE_BREAK_HYPHEN.sub(r"\1\2", text)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("­", "")
    text = text.translate(_PUNCTUATION_FOLD)
    return " ".join(text.split()).casefold()


#: A word split across a line break by the typesetter, not by the author.
_LINE_BREAK_HYPHEN = re.compile("(\\w)[-\u2010\u2011]\\s*\\n\\s*(\\w)")

#: Typography a PDF carries that a transcription usually flattens. Written as
#: escapes so the pairing stays readable and so a linter does not flag the very
#: characters this table exists to fold.
_SMART = "\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u2013\u2014\u2015\u2010\u2011\u00a0"
_PLAIN = "''''" + '"""' + "-----" + " "
_PUNCTUATION_FOLD = str.maketrans(_SMART, _PLAIN)


def _usage_dict(model: str | None, input_tokens: int, output_tokens: int) -> dict[str, Any]:
    """What this agent observed spending: tokens, and the model it called.

    Never money. docs/AGENT_PROTOCOL.md: a price is a vendor fact that changes
    without notice, so cost is derived once where it is needed rather than
    copied into every agent, where N copies age independently.
    """
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "model": model,
    }
