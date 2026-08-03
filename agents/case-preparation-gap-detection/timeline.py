"""Extracts dates from documents, builds a unified timeline, and flags
chronological inconsistencies against document type expectations."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

_FIR_FILED_PATTERN = re.compile(
    r"filed\s*(?:on)?\s*[:\-]?\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE
)


def _fir_filing_date(text: str) -> datetime | None:
    """Only a date explicitly labeled as when the FIR was filed —
    never the earliest date anywhere in the document, which previously
    picked up unrelated dates like a date of birth and silently defeated
    the whole conflict check."""
    match = _FIR_FILED_PATTERN.search(text)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d")  # noqa: DTZ007 — calendar dates from legal documents have no timezone
    except ValueError:
        return None


# Evidence types that should logically be dated on or after the FIR —
# they're collected in response to an incident already reported.
_EVIDENCE_TYPES_EXPECTING_POST_FIR_DATE = {"post_mortem_report"}

_DATE_PATTERNS: list[tuple[str, str]] = [
    (r"\b(\d{4})-(\d{2})-(\d{2})\b", "%Y-%m-%d"),
    (r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", "%d/%m/%Y"),
    (r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b", "%d-%m-%Y"),
]

_MONTH_NAME_PATTERN = re.compile(
    r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(\d{4})\b",
    re.IGNORECASE,
)


def _extract_dates(text: str) -> list[tuple[datetime, str]]:
    found: list[tuple[datetime, str]] = []

    for pattern, fmt in _DATE_PATTERNS:
        for match in re.finditer(pattern, text):
            try:
                dt = datetime.strptime(match.group(0), fmt)  # noqa: DTZ007 — calendar dates from legal documents have no timezone
            except ValueError:
                continue
            found.append((dt, _snippet(text, match.start(), match.end())))

    for match in _MONTH_NAME_PATTERN.finditer(text):
        try:
            dt = datetime.strptime(match.group(0), "%d %B %Y")  # noqa: DTZ007 — calendar dates from legal documents have no timezone
        except ValueError:
            continue
        found.append((dt, _snippet(text, match.start(), match.end())))

    return found

def _snippet(text: str, start: int, end: int, radius: int = 40) -> str:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    return text[lo:hi].strip().replace("\n", " ")


def build_timeline(
    documents: list[dict[str, Any]],
    classification: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    type_by_id = {c["id"]: c["type"] for c in classification}
    documents_by_id = {doc["id"]: doc["text"] for doc in documents}
    timeline: list[dict[str, Any]] = []
    dates_by_doc: dict[str, list[datetime]] = {}

    for doc in documents:
        dates = _extract_dates(doc["text"])
        dates_by_doc[doc["id"]] = [d for d, _ in dates]
        for dt, snippet in dates:
            timeline.append({
                "date": dt.strftime("%Y-%m-%d"),
                "event": snippet,
                "source_doc": doc["id"],
            })

    timeline.sort(key=lambda entry: entry["date"])
    issues = _find_issues(dates_by_doc, type_by_id, documents_by_id)
    return timeline, issues


def _find_issues(
    dates_by_doc: dict[str, list[datetime]],
    type_by_id: dict[str, str],
    documents_by_id: dict[str, str],
) -> list[dict[str, Any]]:
    fir_ids = [doc_id for doc_id, dtype in type_by_id.items() if dtype == "fir"]
    if not fir_ids:
        return []  # nothing to compare against

    fir_date: datetime | None = None
    for doc_id in fir_ids:
        candidate = _fir_filing_date(documents_by_id[doc_id])
        if candidate is not None:
            fir_date = candidate
            break

    if fir_date is None:
        return [{
            "type": "missing_date",
            "description": (
                "An FIR document is present but no clearly labeled filing "
                "date ('filed on YYYY-MM-DD') could be found, so timeline "
                "consistency could not be checked."
            ),
            "docs": fir_ids,
        }]

    issues: list[dict[str, Any]] = []
    for doc_id, dtype in type_by_id.items():
        if dtype not in _EVIDENCE_TYPES_EXPECTING_POST_FIR_DATE:
            continue
        doc_dates = dates_by_doc.get(doc_id, [])
        if not doc_dates:
            continue
        earliest = min(doc_dates)
        if earliest < fir_date:
            issues.append({
                "type": "date_conflict",
                "description": (
                    f"Post-mortem report in '{doc_id}' is dated "
                    f"{earliest.strftime('%Y-%m-%d')}, before the FIR date "
                    f"{fir_date.strftime('%Y-%m-%d')} — a death investigation "
                    f"cannot precede the incident being reported; check whether "
                    f"the FIR was filed late or a date was recorded incorrectly."
                ),
                "docs": [doc_id],
            })

    return issues
