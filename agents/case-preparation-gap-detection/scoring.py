"""Rolls up classification, timeline, and evidence findings into one score."""

from __future__ import annotations

from typing import Any

_BASE_SCORE = 100
_PENALTY_PER_MISSING_EVIDENCE = 15
_PENALTY_PER_TIMELINE_ISSUE = 10
_PENALTY_PER_LOW_CONFIDENCE_DOC = 5
_LOW_CONFIDENCE_THRESHOLD = 0.5


def compute_readiness_score(
    classification: list[dict[str, Any]],
    timeline_issues: list[dict[str, Any]],
    missing_evidence: list[dict[str, Any]],
) -> int:
    score = _BASE_SCORE
    score -= _PENALTY_PER_MISSING_EVIDENCE * len(missing_evidence)
    score -= _PENALTY_PER_TIMELINE_ISSUE * len(timeline_issues)

    low_confidence_docs = [
        c for c in classification if c["confidence"] < _LOW_CONFIDENCE_THRESHOLD
    ]
    score -= _PENALTY_PER_LOW_CONFIDENCE_DOC * len(low_confidence_docs)

    return max(0, min(100, score))