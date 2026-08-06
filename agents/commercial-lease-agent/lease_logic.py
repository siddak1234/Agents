"""Business logic for the commercial-lease-agent.

Two independent pieces of work live here:
  - calculate_deadline: pure date arithmetic, no external dependency.
  - extract_clauses: reads lease pages, calls an LLM, returns clause records.

Nothing here knows about the agentcall protocol. Every function takes and
returns plain Python values, so this file can be tested by itself.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

VALID_CLAUSE_TYPES = ("renewal", "financial", "maintenance")


class LeaseLogicError(Exception):
    """Base class for business-level failures a caller should see as an error."""


class MissingCredentialError(LeaseLogicError):
    """Raised when a required credential is not configured."""


class ModelCallError(LeaseLogicError):
    """Raised when the call to the model itself fails. May be transient."""


def calculate_deadline(lease_end_date: str, notice_period_days: int) -> str:
    """Return the date that is `notice_period_days` days before `lease_end_date`."""
    try:
        end = _dt.date.fromisoformat(lease_end_date)
    except ValueError as exc:
        raise LeaseLogicError(f"'lease_end_date' must be a real YYYY-MM-DD date: {exc}") from None
    deadline = end - _dt.timedelta(days=notice_period_days)
    return deadline.isoformat()


def extract_clauses(lease_pages: list[str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Extract renewal/financial/maintenance clauses using an LLM tool call.

    Returns (clauses, usage). Raises MissingCredentialError if the API key is
    not configured, or ModelCallError if the call to the model fails.
    """
    import os

    import anthropic  # imported here, not at module load — rule 6: describe stays cheap

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise MissingCredentialError("ANTHROPIC_API_KEY is not set")

    client = anthropic.Anthropic(api_key=api_key)
    tool = _build_tool_schema()
    prompt = _build_prompt(lease_pages)

    try:
        response = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=4096,
            tools=[tool],
            tool_choice={"type": "tool", "name": "record_clauses"},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # network or API-side failure
        raise ModelCallError(f"model call failed: {exc}") from exc

    raw_clauses = _extract_tool_input(response)
    clauses = [_clean_clause(c, lease_pages) for c in raw_clauses if _is_well_formed(c)]

    usage = {
        "input_tokens": getattr(response.usage, "input_tokens", 0),
        "output_tokens": getattr(response.usage, "output_tokens", 0),
        "cost_micros": 0,  # left at 0 until a per-model rate table is added
    }
    return clauses, usage


def _build_tool_schema() -> dict[str, Any]:
    return {
        "name": "record_clauses",
        "description": "Record every renewal, financial, or maintenance clause found in the lease.",
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


def _build_prompt(lease_pages: list[str]) -> str:
    numbered = "\n\n".join(f"--- page {i + 1} ---\n{text}" for i, text in enumerate(lease_pages))
    return (
        "Read this commercial lease, page by page. Find every clause about "
        "renewal terms, financial terms, or maintenance responsibilities. "
        "For each one, quote the exact source text, note which page it is "
        "on, and rate your confidence from 0.0 to 1.0. Call record_clauses "
        "with everything you find, and nothing else.\n\n" + numbered
    )


def _extract_tool_input(response: Any) -> list[dict[str, Any]]:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_clauses":
            return block.input.get("clauses", [])
    raise ModelCallError("model did not return a record_clauses tool call")


def _is_well_formed(clause: dict[str, Any]) -> bool:
    required = ("clause_type", "text_quote", "page_number", "confidence_score")
    return isinstance(clause, dict) and all(k in clause for k in required)


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