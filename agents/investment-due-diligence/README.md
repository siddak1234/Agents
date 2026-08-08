# investment-due-diligence

Evaluates residential real estate investment opportunities for the Indian
residential market (prices in INR) for individual investors and prospective
home buyers. Given a property's basic details (address or listing URL, asking
price, area), it builds a verified property profile, analyzes financial
attractiveness, assesses the location's infrastructure and growth outlook,
identifies risks, and returns an evidence-backed **BUY / NEGOTIATE / REJECT**
recommendation with a confidence score.

It will not: evaluate commercial or non-residential property, place a
bid, file paperwork, or give legal advice.

## Project layout

```
investment-due-diligence/
├── agent_main.py     # protocol adapter only -- parse, validate, dispatch, format
├── agent.yaml         # manifest
├── README.md
├── analysis.py        # capabilities 1-4 (property, financial, location, risk)
├── recommendation.py  # capability 5 (LLM synthesis)
├── clients.py         # Tavily + Groq clients, API keys, timeouts
├── budget.py          # deadline_ms → per-call timeout slices
└── tests/
    ├── test_agent.py         # the envelope, over a real subprocess
    ├── test_capabilities.py  # capability composition + accounting, clients stubbed
    ├── test_scoring.py       # the scoring/extraction helpers, one at a time
    └── golden/
        └── recommendation_tool_schema.json   # the schema sent to the model
```

## Capabilities

| Capability | What it does | Calls out to |
|---|---|---|
| `describe` | Reports name + capability list. Free. | nothing |
| `property_intelligence` | Builds a verified property profile (builder, type, configuration, location, price/sqft). | Tavily (best-effort) |
| `financial_analysis` | Market position vs. comparables, rental yield, ROI. | Tavily |
| `location_infrastructure_analysis` | Connectivity, amenities, planned infrastructure, growth outlook. | Tavily |
| `risk_assessment` | Builder/legal, environmental, and market risk. | Tavily |
| `investment_recommendation` | Synthesizes the above into a final call with reasoning. | Groq (`llama-3.3-70b-versatile`) |

A typical caller runs `property_intelligence` first, feeds its output
into `financial_analysis`, `location_infrastructure_analysis`, and
`risk_assessment`, then feeds all of those into
`investment_recommendation`. Nothing in this agent enforces that
order — each capability only requires the specific fields listed in its
schema in `agent.yaml`.

### Inputs are intentionally loose

Almost nothing beyond `location` is a hard requirement, because a real
caller rarely has every field filled in. Each capability degrades
instead of rejecting when something's missing:

- **`financial_analysis`** needs only `location`. Give it `price_per_sqft`
  directly, or `asking_price_inr` + `area_sqft` (it derives
  `price_per_sqft` itself), or neither (it still estimates rental yield
  and ROI from the location alone). `market_value`/`premium_percent` come
  back `null` instead of a guess whenever the comparison cannot honestly
  be made — either no price was supplied, or the search turned up no
  comparable rate to measure it against.
- **`risk_assessment`** needs only `location`. `builder` and
  `property_type` sharpen the result when supplied; their absence shows
  up as a line in `identified_risks`, not a failure.
- **`investment_recommendation`** takes whatever evidence you have — the
  raw output objects from any of the other capabilities
  (`property_intelligence`, `financial_analysis`,
  `location_infrastructure_analysis`, `risk_assessment`), the convenience
  shortcuts (`financial_score`, `location_score`, `overall_risk`), or a mix.
  It only fails `invalid_request` when the caller sends literally none of
  that.

## Running it directly

```bash
echo '{
  "protocol": "agentcall/v1",
  "capability": "property_intelligence",
  "input": {
    "address": "Prestige Lakeside Habitat, Whitefield, Bangalore",
    "asking_price_inr": 14000000,
    "area_sqft": 1650
  },
  "request_id": "demo-1",
  "deadline_ms": 30000
}' | python3 agent_main.py
```

## Configuration

Set these in `.env` (never commit real keys):

| Variable | Required by | Purpose |
|---|---|---|
| `TAVILY_API_KEY` | `financial_analysis`, `location_infrastructure_analysis`, `risk_assessment`; used best-effort by `property_intelligence` | Search for comparables, locality news, builder track record, infrastructure updates |
| `GROQ_API_KEY` | `investment_recommendation` | LLM reasoning for the final recommendation |

`property_intelligence` degrades gracefully without `TAVILY_API_KEY` —
it falls back to what the caller supplied instead of failing, since
search is corroboration, not the property's identity. Every other
capability that needs a key returns `unavailable` (not a crash) when
that key is missing.

## Design notes

- **Protocol vs. logic.** `agent_main.py` parses stdin, validates shape,
  and builds the envelope — nothing else. All real work lives in
  `analysis.py` (capabilities 1-4) and `recommendation.py` (capability
  5), and both talk to the outside world only through `clients.py`.
- **Errors are plain built-ins, on purpose.** There's no custom
  exception hierarchy. Business logic raises whichever built-in
  exception already means the right thing, and `dispatch()` in
  `agent_main.py` is the single place that maps it onto an envelope:

  | Raised as | Envelope | retryable |
  |---|---|---|
  | `ValueError` | `invalid_request` | false |
  | `RuntimeError` | `unavailable` | false (missing key, a 4xx) |
  | `ConnectionError` | `unavailable` | true (unreachable, a 5xx/429) |
  | `TimeoutError` | `timeout` | true |

- **Timeouts are budgeted from `deadline_ms`.** `budget.py` splits the
  remaining wall-clock across the outbound calls a capability still has
  to make (capped at 12s each), so three Tavily searches cannot schedule
  45s against a 30s deadline. Omit `deadline_ms` and the agent assumes
  30s.
- **Stdlib only.** `clients.py` talks to Tavily and Groq via raw
  `urllib`, so `command:` in `agent.yaml` stays a bare
  `["python3", "agent_main.py"]` — nothing to install.
- **Heuristic extraction, not NLP.** `analysis.py` uses regex and
  keyword heuristics over Tavily search snippets — deliberately simple,
  meant to be swapped for something sturdier without touching
  `agent_main.py`. Two limits are handled explicitly rather than left to
  chance: hints match on word boundaries (so "shop" does not fire inside
  "Bishop"), and a risk keyword preceded by a negation does not count (so
  coverage reading "no oversupply" is not reported as oversupply). Both
  remain heuristics — "no doubt there is a slowdown" still reads as negated.
- **Evidence into Groq is size-capped.** `investment_recommendation`
  rejects caller-supplied evidence objects larger than 8 KiB so the
  prompt cannot grow without bound.
- **The recommendation comes back through a forced tool call.**
  `RECOMMENDATION_TOOL` in `recommendation.py` is the tool definition, the
  forced `tool_choice`, and the shape `_validate_output` checks, so the
  schema asked of the model cannot drift from the one the envelope
  publishes; `tests/golden/` pins it. If the model returns no usable
  `recommendation` or `confidence_percent`, the capability fails
  `unavailable` (retryable) rather than substituting a default — an
  invented "NEGOTIATE at 50%" is indistinguishable from a real call, and
  this is the one field the agent exists to produce.
- **`usage` reports what was actually spent.** Tavily bills 1 credit per
  `search_depth: "basic"` search ($0.008), and Groq bills tokens; both are
  recorded in `clients.py` as they are spent and read once in
  `agent_main.py` where the envelope is built. A request that paid for two
  searches and then failed reports those two searches rather than zero.
  OpenStreetMap geocoding is free and is not counted.

## Tests

```bash
python3 -m unittest discover -s tests
```

`test_agent.py` runs the real `agent_main.py` as a subprocess, the same
way the orchestrator does, so stdout hygiene and the exit code are
checked, not assumed. `test_capabilities.py` covers what the subprocess
tests cannot reach without a key: it stubs `tavily_search`/`geocode` where
the assertion is about scoring composition, and replaces
`urllib.request.urlopen` where it is about the envelope, so the real
client code, error mapping and spend tally all run.

None of them touch the network. The envelope tests do set fake API keys,
but the transport itself is replaced, so no socket is opened and a real
key cannot be picked up from the environment either.

## Wiring it into the repository

Two edits outside this folder, made by hand:

1. Add to the root `registry.yaml`:
   ```yaml
   agents:
     - path: agents/investment-due-diligence
   ```
2. Add a row to the agents table in the root `README.md`:
   ```markdown
   | [`investment-due-diligence`](./agents/investment-due-diligence) | active | `property_intelligence`, `financial_analysis`, `location_infrastructure_analysis`, `risk_assessment`, `investment_recommendation` |
   ```
