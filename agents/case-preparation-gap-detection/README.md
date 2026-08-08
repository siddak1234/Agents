# case-preparation-gap-detection

Reads a litigation case's documents (FIR, witness statements, medical and
forensic reports, charge sheets) and returns a structured report of
preparation gaps for a lawyer or litigation team preparing to file.

It does not judge the case, decide guilt or innocence, or recommend legal
strategy — it only flags what a lawyer would otherwise have to catch by
manually cross-referencing every document.

## Capabilities

- `describe` — reports this agent's name and capabilities.
- `review_case` — analyses a set of case documents and returns a readiness
  score, missing evidence, and timeline inconsistencies. Pure rule-based
  logic — no model calls, no network access, no credentials required.

## How it works

1. **Classification** — tags each document by type (FIR, medical report,
   charge sheet, etc.) via keyword matching.
2. **Timeline** — extracts dates from every document, merges them into one
   chronology, and flags a **post-mortem report** dated before the FIR.
   Only that type: a medical examination or a witness statement can
   legitimately predate a late-filed FIR, so flagging those would be noise.
   Numeric dates are read **day-first** (`DD/MM/YYYY`), per Indian practice.
3. **Evidence completeness** — a bundled rule table (`data/evidence_rules.json`)
   checks whether facts mentioned in the case (a weapon, an injury, a death)
   have a corresponding supporting document; flags what's missing.
4. **Scoring** — rolls up missing evidence, timeline issues, and low-confidence
   classifications into a single 0–100 readiness score.

## Running it directly

```
echo '{"protocol": "agentcall/v1", "capability": "review_case", "input": {"documents": [{"id": "fir1", "text": "First Information Report FIR No. 45/2024 filed 2024-01-10. A knife was recovered from the scene."}, {"id": "med1", "text": "Medical examination report dated 2024-01-12."}]}}' | python3 agent_main.py
```
