"""Classifies each document into a legal document type via keyword rules."""

from __future__ import annotations

from typing import Any

# Order matters: checked top to bottom, first match wins.
_TYPE_RULES: list[tuple[str, list[str]]] = [
    ("fir", ["first information report", "fir no", "f.i.r"]),
    ("charge_sheet", ["charge sheet", "chargesheet", "charges framed"]),
    ("medical_report", ["medical examination", "medical report", "examined the patient"]),
    ("post_mortem_report", ["post-mortem", "postmortem", "post mortem", "autopsy"]),
    ("forensic_report", ["forensic", "fingerprint", "ballistic", "dna analysis"]),
    ("witness_statement", ["statement of witness", "i state that", "deposition"]),
]

_DEFAULT_TYPE = "other"


def classify_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for doc in documents:
        doc_type, confidence = _classify_one(doc["text"])
        results.append({"id": doc["id"], "type": doc_type, "confidence": confidence})
    return results


def _classify_one(text: str) -> tuple[str, float]:
    text_lower = text.lower()
    for doc_type, keywords in _TYPE_RULES:
        matches = sum(1 for kw in keywords if kw in text_lower)
        if matches:
            # More matching phrases -> higher confidence, capped at 0.95.
            confidence = min(0.6 + 0.15 * matches, 0.95)
            return doc_type, round(confidence, 2)
    return _DEFAULT_TYPE, 0.3