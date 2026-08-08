# commercial-lease-agent

Extracts renewal, financial, and maintenance clauses from commercial lease
documents, and calculates notice deadlines. Built to be called by an
orchestrator or script — not run and read directly by a person.

## What it does

- **`extract_clauses`** — given a lease split into pages, returns every
  renewal, financial, and maintenance clause found, each with its exact
  quote, its real physical page number, and a confidence score. Uses an LLM
  with a tool schema, so the response is always structured JSON, never free
  text to parse.
- **`calculate_deadline`** — given a lease end date and a notice period in
  days, returns the deadline date. Pure date arithmetic; callable on its
  own, with no dependency on `extract_clauses` running first.

**The two do not chain by themselves.** `extract_clauses` returns quotes, and
`calculate_deadline` wants a date and an integer, so a caller wanting a
renewal deadline has to read the notice period out of a renewal clause's
`text_quote` and find the lease end date elsewhere — the term clause is not
one of the three types extracted. Closing that gap is a capability change, not
a configuration one.

## What it will not do

- It does not read PDF or Word files itself — the caller splits the
  document into page-text strings before calling `extract_clauses`.
- It does not store, cache, or remember any lease it has processed. Every
  call is independent.
- It does not decide what a human sees — rendering the result is the
  calling program's job.

## Running it

`calculate_deadline` needs no credentials. `extract_clauses` needs
`ANTHROPIC_API_KEY` set in the environment.

```bash
echo '{"protocol": "agentcall/v1", "capability": "calculate_deadline", "input": {"lease_end_date": "2028-12-31", "notice_period_days": 90}, "request_id": "", "deadline_ms": 120000}' | uv run python agent_main.py
```

```bash
echo '{"protocol": "agentcall/v1", "capability": "extract_clauses", "input": {"lease_pages": ["Page one lease text...", "Page two lease text..."]}, "request_id": "", "deadline_ms": 120000}' | uv run python agent_main.py
```

## Configuration

| Variable | Required for | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | `extract_clauses` | none — required |
| `LEASE_AGENT_MODEL` | `extract_clauses` | `claude-sonnet-5` |
| `LEASE_AGENT_CHUNK_SIZE` | `extract_clauses` | `20` pages per model call |

Long leases are processed in chunks of `LEASE_AGENT_CHUNK_SIZE` pages per
model call, so a 250-page lease results in ~13 calls, merged into one
clause list — not one call for the whole document. Because each chunk is one
billed call, `lease_pages` is capped at **1000 pages and 4,000,000 characters**
— the characters are what the request actually costs, and a page count alone
bounds neither calls nor tokens. A longer document is refused with
`invalid_request` rather than silently costing more, and the caller splits it,
passing `first_page_number`.

## Quotes are checked, and the misses are reported

Every clause in `clauses` has had its `text_quote` found in the pages you
passed in. One that could not be found comes back in `unverified_clauses`
with `page_number: null` and the model's own `claimed_page_number` kept
separately.

Nothing is discarded. A citation you cannot check is worse than no citation —
but deleting it silently is worse still, because it makes "this lease has no
renewal clause" and "the model paraphrased the renewal clause" the same
answer, and for a legal document that is the more dangerous one to guess at.

Matching folds what PDF extraction and transcription differ on: line wrapping,
hyphenation at a line break, typographic quotes and dashes, ligatures and soft
hyphens. It does not fold anything that changes meaning — figures, negation
and which party owes the obligation must still match exactly.

**Known limitation.** Because whitespace is collapsed, a quote can match across
a column gutter in a table — text that was never contiguous prose on the page.
Treat a quote spanning what looks like tabular data as needing a human's eye.

## Page numbers when you split a document

`page_number` is the position within `lease_pages`, offset by
`first_page_number`. If you send part of a lease, pass the real page number of
`lease_pages[0]`:

```json
{"lease_pages": ["…page 1001…", "…page 1002…"], "first_page_number": 1001}
```

Omit it and the second part reports its clauses as pages 1 and 2.

## Example output

`calculate_deadline`, success:
```json
{"protocol": "agentcall/v1", "ok": true, "capability": "calculate_deadline", "output": {"deadline_date": "2028-10-02"}, "usage": {"input_tokens": 0, "output_tokens": 0, "model": null}, "error": null}
```

`extract_clauses`, success:
```json
{"protocol": "agentcall/v1", "ok": true, "capability": "extract_clauses", "output": {"clauses": [{"clause_type": "renewal", "text_quote": "This Lease shall renew automatically for successive one-year terms unless either party provides 90 days written notice of non-renewal.", "page_number": 1, "confidence_score": 0.95}], "unverified_clauses": []}, "usage": {"input_tokens": 412, "output_tokens": 96, "model": "claude-sonnet-5"}, "error": null}
```

`extract_clauses`, missing credential:
```json
{"protocol": "agentcall/v1", "ok": false, "capability": "extract_clauses", "output": null, "usage": {"input_tokens": 0, "output_tokens": 0, "model": null}, "error": {"type": "unavailable", "message": "ANTHROPIC_API_KEY is not set", "retryable": false}}
```

## Errors

| Situation | `error.type` | `retryable` |
|---|---|---|
| Missing or malformed `lease_pages`, `lease_end_date`, or `notice_period_days` | `invalid_request` | `false` |
| `lease_pages` longer than 1000 | `invalid_request` | `false` |
| `notice_period_days` out of range — usually a unit mix-up, seconds for days | `invalid_request` | `false` |
| `ANTHROPIC_API_KEY` not set, or rejected by the API | `unavailable` | `false` — configuration does not fix itself |
| The API rejects the request (excerpt too large) | `invalid_request` | `false` — your pages or `LEASE_AGENT_CHUNK_SIZE` |
| Rate limited, connection dropped, or a 5xx from the API | `unavailable` | `true` |
| The model returned nothing matching the declared types | `unavailable` | `false` |
| Anything unanticipated | `internal` | `false` |

`retryable` is read off the table in `docs/INTERN_BRIEF.md`, not judged per
case: `unavailable` is retryable only when the cause is transient.
