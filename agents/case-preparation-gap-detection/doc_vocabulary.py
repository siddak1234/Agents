"""Single source of phrase vocabulary per document type.

Shared by classifier.py and evidence_rules.py so the two can't drift apart —
that drift is what let a witness statement get flagged as its own missing
evidence (classified as "other" while the evidence rule used a separate,
overlapping keyword list).
"""

from __future__ import annotations

DOCUMENT_VOCABULARY: dict[str, list[str]] = {
    "fir": ["first information report", "fir no", "f.i.r"],
    "charge_sheet": ["charge sheet", "chargesheet", "charges framed"],
    "post_mortem_report": [
        "post-mortem",
        "postmortem",
        "post mortem",
        "autopsy",
        "examination of the deceased",
        "cause of death",
    ],
    "medical_report": [
        "medical examination report",
        "examined the patient",
        "injuries sustained",
    ],
    "forensic_report": ["forensic", "fingerprint", "ballistic", "dna analysis"],
    "witness_statement": [
        "statement of witness",
        "i state that",
        "deposition",
        "i saw",
        "eyewitness",
        "witnessed the",
    ],
}
