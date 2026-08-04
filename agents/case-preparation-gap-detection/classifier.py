"""Classifies each document into a legal document type, scoring every type
against its vocabulary rather than stopping at the first match — a
first-match order let a post-mortem worded around "medical examination of
the deceased" get classified as medical_report because that type happened
to be checked first."""

from __future__ import annotations

import re
from typing import Any

from doc_vocabulary import DOCUMENT_VOCABULARY

_DEFAULT_TYPE = "other"


def classify_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for doc in documents:
        doc_type, confidence = _classify_one(doc["text"])
        results.append({"id": doc["id"], "type": doc_type, "confidence": confidence})
    return results


def _classify_one(text: str) -> tuple[str, float]:
    text_lower = text.lower()
    best_type, best_matches = _DEFAULT_TYPE, 0

    for doc_type, keywords in DOCUMENT_VOCABULARY.items():
        matches = sum(
            1 for kw in keywords if re.search(rf"\b{re.escape(kw)}\b", text_lower)
        )
        if matches > best_matches:
            best_type, best_matches = doc_type, matches

    if best_matches == 0:
        return _DEFAULT_TYPE, 0.3
    confidence = min(0.6 + 0.15 * best_matches, 0.95)
    return best_type, round(confidence, 2)
