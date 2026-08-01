"""Extracts dates from documents, builds a unified timeline, and flags
chronological inconsistencies against document type expectations."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# Evidence types that should logically be dated on or after the FIR —
# they're collected in response to an incident already reported.
_EVIDENCE_TYPES_EXPECTING_POST_FIR_DATE = {
    "medical_report", "post_mortem_report", "forensic_report",
}

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
                dt = datetime.strptime(match.group(0), fmt)
            except ValueError:
                continue
            found.append((dt, _snippet(text, match.start(), match.end())))

    for match in _MONTH_NAME_PATTERN.finditer(text):
        try:
            dt = datetime.strptime(match.group(0), "%d %B %Y")
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
    issues = _find_issues(dates_by_doc, type_by_id)
    return timeline, issues


def _find_issues(
    dates_by_doc: dict[str, list[datetime]],
    type_by_id: dict[str, str],
) -> list[dict[str, Any]]:
    fir_dates = [
        dt
        for doc_id, dtype in type_by_id.items()
        if dtype == "fir"
        for dt in dates_by_doc.get(doc_id, [])
    ]
    if not fir_dates:
        return []  # nothing to compare against

    fir_date = min(fir_dates)
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
                    f"{dtype.replace('_', ' ')} in '{doc_id}' is dated "
                    f"{earliest.strftime('%Y-%m-%d')}, before the FIR date "
                    f"{fir_date.strftime('%Y-%m-%d')}"
                ),
                "docs": [doc_id],
            })

    return issues
