"""Orchestrates the case review pipeline. No protocol logic here."""

from __future__ import annotations

from typing import Any

from classifier import classify_documents
from evidence_rules import check_missing_evidence
from scoring import compute_readiness_score
from timeline import build_timeline


def review_case(documents: list[dict[str, Any]]) -> dict[str, Any]:
    classification = classify_documents(documents)
    timeline, timeline_issues = build_timeline(documents,classification)
    missing_evidence = check_missing_evidence(documents, classification)

    readiness_score = compute_readiness_score(
        classification=classification,
        timeline_issues=timeline_issues,
        missing_evidence=missing_evidence,
    )

    recommendations = _build_recommendations(missing_evidence, timeline_issues)

    return {
        "readiness_score": readiness_score,
        "document_classification": classification,
        "timeline": timeline,
        "timeline_issues": timeline_issues,
        "missing_evidence": missing_evidence,
        "recommendations": recommendations,
    }


def _build_recommendations(
    missing_evidence: list[dict[str, Any]],
    timeline_issues: list[dict[str, Any]],
) -> list[str]:
    recs = [
        f"Obtain {item['expected'].replace('_', ' ')} ({item['reason']})"
        for item in missing_evidence
    ]
    recs.extend(
        f"Resolve timeline conflict: {issue['description']}"
        for issue in timeline_issues
    )
    return recs
