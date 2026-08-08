"""Checks for evidence the case's own facts imply but no document supplies."""

from __future__ import annotations

import json
import re
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
                if re.search(rf"\b{re.escape(keyword)}\b", text_lower):
                    missing.append({
                        "expected": expected_type,
                        "reason": rule["reason"],
                        "triggered_by": doc["id"],
                        "matched": keyword,  # also closes item 10
                    })
                    break  # one hit per rule per document is enough

    return _dedupe(missing)


def _dedupe(missing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per distinct finding, with every triggering document listed
    together — three docs mentioning a knife should produce one
    recommendation, not three identical ones.

    Keyed on the reason as well as the expected type. Keying on the type alone
    merged findings that are not the same finding: three rules in
    data/evidence_rules.json expect a `forensic_report` (weapon recovery,
    video/camera, physical trace), so a case with a knife, CCTV and a
    bloodstain collapsed into one entry that kept the first rule's `reason` and
    `matched` and then attributed them to all three documents — asserting that
    a witness statement about CCTV had matched "knife". Two entirely different
    case files produced byte-identical output.
    """
    by_finding: dict[tuple[str, str], dict[str, Any]] = {}
    for item in missing:
        key = (item["expected"], item["reason"])
        if key not in by_finding:
            by_finding[key] = {
                "expected": item["expected"],
                "reason": item["reason"],
                "triggered_by": [item["triggered_by"]],
                "matched": item["matched"],
            }
        else:
            by_finding[key]["triggered_by"].append(item["triggered_by"])
    # One document can satisfy several keywords of the same rule, which listed
    # it more than once against a docstring promising each triggering document.
    for entry in by_finding.values():
        entry["triggered_by"] = list(dict.fromkeys(entry["triggered_by"]))
    return list(by_finding.values())
