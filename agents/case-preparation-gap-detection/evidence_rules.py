"""Checks for evidence the case's own facts imply but no document supplies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_RULES_PATH = Path(__file__).parent / "data" / "evidence_rules.json"


def _load_rules() -> list[dict[str, Any]]:
    with _RULES_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def check_missing_evidence(
    documents: list[dict[str, Any]],
    classification: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rules = _load_rules()
    present_types = {c["type"] for c in classification}
    missing: list[dict[str, Any]] = []

    for doc in documents:
        text_lower = doc["text"].lower()
        for rule in rules:
            expected_type = rule["expected"]
            if expected_type in present_types:
                continue  # already satisfied by some document
            for keyword in rule["trigger_keywords"]:
                if keyword in text_lower:
                    missing.append({
                        "expected": expected_type,
                        "reason": rule["reason"],
                        "triggered_by": doc["id"],
                    })
                    break  # one hit per rule per document is enough

    return _dedupe(missing)


def _dedupe(missing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for item in missing:
        key = (item["expected"], item["triggered_by"])
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result