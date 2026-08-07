"""Business logic for the commercial-lease-agent.

Two independent pieces of work live here:
  - calculate_deadline: pure date arithmetic, no external dependency.
  - extract_clauses: reads lease pages in bounded chunks, calls an LLM per
    chunk using multi-shot prompting and a tool schema, validates the
    model's output against the types agent.yaml declares, and merges
    results across chunks.

Nothing here knows about the agentcall protocol. Every function takes and
returns plain Python values, so this file can be tested by itself.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
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

# Published per-million-token USD list pricing
# (platform.claude.com/docs/en/about-claude/pricing, checked 2026-08-06).
# Sonnet 5 is on introductory pricing through 2026-08-31, then moves to
# $3 / $15. Override via LEASE_AGENT_INPUT_PRICE_PER_MTOK /
# LEASE_AGENT_OUTPUT_PRICE_PER_MTOK if pricing has since changed.
_DEFAULT_PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}

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

class ModelCallError(LeaseLogicError):
    """Raised when a model call fails, or returns output that doesn't pass
    validation. May be transient. Carries usage accumulated before the
    failure, since earlier chunk calls in the same request may already have
    been billed even though this one failed.
    """

    def __init__(self, message: str, usage: dict[str, int] | None = None) -> None:
        super().__init__(message)
        self.usage = usage or {"input_tokens": 0, "output_tokens": 0, "cost_micros": 0}


def calculate_deadline(lease_end_date: str, notice_period_days: int) -> str:
    """Return the date that is `notice_period_days` days before `lease_end_date`."""
    try:
        end = _dt.date.fromisoformat(lease_end_date)
    except ValueError as exc:
        raise LeaseLogicError(f"'lease_end_date' must be a real YYYY-MM-DD date: {exc}") from None
    deadline = end - _dt.timedelta(days=notice_period_days)
    return deadline.isoformat()


def extract_clauses(lease_pages: list[str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Extract renewal/financial/maintenance clauses using an LLM, chunked.

    Returns (clauses, usage). Raises MissingDependencyError if the anthropic
    package isn't installed, MissingCredentialError if the API key is not
    configured (neither bills anything), or ModelCallError if a call fails
    or returns unusable output (usage attached, reflecting anything already
    billed in this request).
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
    input_tokens = 0
    output_tokens = 0

    for start, end in _chunk_ranges(len(lease_pages), chunk_size):
        chunk = lease_pages[start:end]
        prompt = _build_prompt(chunk, start)

        try:
            response = client.messages.create(
                model=model,
                max_tokens=_MAX_OUTPUT_TOKENS_PER_CHUNK,
                tools=[tool],
                tool_choice={"type": "tool", "name": "record_clauses"},
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:  # network or API-side failure — nothing from this call was billed
            raise ModelCallError(
                f"model call failed on pages {start + 1}-{end}: {exc}",
                usage=_usage_dict(model, input_tokens, output_tokens),
            ) from exc

        # This call's tokens are billed now, regardless of what happens next.
        input_tokens += getattr(response.usage, "input_tokens", 0)
        output_tokens += getattr(response.usage, "output_tokens", 0)

        try:
            raw_clauses = _extract_tool_input(response)
        except ModelCallError as exc:
            exc.usage = _usage_dict(model, input_tokens, output_tokens)
            raise

        valid = [c for c in raw_clauses if _is_well_formed(c)]
        if raw_clauses and not valid:
            # The model responded, and tokens were billed, but nothing it
            # returned matches the schema we declared — that's the model's
            # dependency failing, not a bad request from our caller.
            raise ModelCallError(
                f"model returned {len(raw_clauses)} clause(s) on pages {start + 1}-{end} "
                "that failed validation against the declared schema",
                usage=_usage_dict(model, input_tokens, output_tokens),
            )
        if len(valid) < len(raw_clauses):
            print(
                f"lease_logic: dropped {len(raw_clauses) - len(valid)} malformed "
                f"clause(s) on pages {start + 1}-{end}",
                file=sys.stderr,
            )

        all_clauses.extend(_clean_clause(c, lease_pages) for c in valid)

    return all_clauses, _usage_dict(model, input_tokens, output_tokens)


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
            "properties": {
                "clauses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["clause_type", "text_quote", "page_number", "confidence_score"],
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


def _clean_clause(clause: dict[str, Any], lease_pages: list[str]) -> dict[str, Any]:
    """Recompute page_number from the real page text instead of trusting the model's guess."""
    quote = str(clause["text_quote"])
    page_number = _locate_page(quote, lease_pages, fallback=int(clause["page_number"]))
    confidence = max(0.0, min(1.0, float(clause["confidence_score"])))
    return {
        "clause_type": clause["clause_type"],
        "text_quote": quote,
        "page_number": page_number,
        "confidence_score": confidence,
    }


def _locate_page(quote: str, lease_pages: list[str], fallback: int) -> int:
    for i, page_text in enumerate(lease_pages):
        if quote in page_text:
            return i + 1
    return max(1, min(fallback, len(lease_pages)))


def _usage_dict(model: str, input_tokens: int, output_tokens: int) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_micros": _estimate_cost_micros(model, input_tokens, output_tokens),
    }


def _estimate_cost_micros(model: str, input_tokens: int, output_tokens: int) -> int:
    """Estimate cost in micros (1,000,000 micros = $1 USD) from real token counts.

    Best-effort estimate from published list pricing (see
    _DEFAULT_PRICE_PER_MTOK), overridable via LEASE_AGENT_INPUT_PRICE_PER_MTOK
    / LEASE_AGENT_OUTPUT_PRICE_PER_MTOK — not an authoritative billing figure.
    """
    in_rate, out_rate = _resolve_price_per_mtok(model)
    if in_rate is None or out_rate is None:
        print(f"lease_logic: no known price for model {model!r}; cost_micros will be 0", file=sys.stderr)
        return 0
    cost_dollars = (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate
    return round(cost_dollars * 1_000_000)


def _resolve_price_per_mtok(model: str) -> tuple[float | None, float | None]:
    override_in = os.environ.get("LEASE_AGENT_INPUT_PRICE_PER_MTOK")
    override_out = os.environ.get("LEASE_AGENT_OUTPUT_PRICE_PER_MTOK")
    if override_in and override_out:
        try:
            return float(override_in), float(override_out)
        except ValueError:
            pass
    return _DEFAULT_PRICE_PER_MTOK.get(model, (None, None))
