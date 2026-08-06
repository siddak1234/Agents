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
| `LEASE_AGENT_INPUT_PRICE_PER_MTOK` | `extract_clauses` cost estimate | published Sonnet 5 rate |
| `LEASE_AGENT_OUTPUT_PRICE_PER_MTOK` | `extract_clauses` cost estimate | published Sonnet 5 rate |

Long leases are processed in chunks of `LEASE_AGENT_CHUNK_SIZE` pages per
model call, so a 250-page lease results in ~13 calls, merged into one
clause list — not one call for the whole document.

## Example output

`calculate_deadline`, success:
```json
{"protocol": "agentcall/v1", "ok": true, "capability": "calculate_deadline", "output": {"deadline_date": "2028-10-02"}, "usage": {"input_tokens": 0, "output_tokens": 0, "cost_micros": 0}, "error": null}
```

`extract_clauses`, success:
```json
{"protocol": "agentcall/v1", "ok": true, "capability": "extract_clauses", "output": {"clauses": [{"clause_type": "renewal", "text_quote": "This Lease shall renew automatically for successive one-year terms unless either party provides 90 days written notice of non-renewal.", "page_number": 1, "confidence_score": 0.95}]}, "usage": {"input_tokens": 412, "output_tokens": 96, "cost_micros": 1784}, "error": null}
```

`extract_clauses`, missing credential:
```json
{"protocol": "agentcall/v1", "ok": false, "capability": "extract_clauses", "output": null, "usage": {"input_tokens": 0, "output_tokens": 0, "cost_micros": 0}, "error": {"type": "unavailable", "message": "ANTHROPIC_API_KEY is not set", "retryable": false}}
```

## Errors

| Situation | `error.type` |
|---|---|
| Missing or malformed `lease_pages`, `lease_end_date`, or `notice_period_days` | `invalid_request` |
| `ANTHROPIC_API_KEY` not set | `unavailable` |
| The model call itself fails (network, API error) | `unavailable`, `retryable: true` |
| Anything unanticipated | `internal` |