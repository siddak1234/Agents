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
├── clients.py         # the Claude client: web search + a forced tool, per call
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
| `property_intelligence` | Builds a verified property profile (builder, type, configuration, location, price/sqft). | Claude + web search |
| `financial_analysis` | Market position vs. comparables, rental yield, ROI. | Claude + web search |
| `location_infrastructure_analysis` | Connectivity, amenities, planned infrastructure, growth outlook. | Claude + web search |
| `risk_assessment` | Builder/legal, environmental, and market risk. | Claude + web search |
| `investment_recommendation` | Synthesizes the above into a final call with reasoning. | Claude |

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
| `ANTHROPIC_API_KEY` | every capability except `describe` | Claude, with the server-side web search tool, does the research and reports through a per-capability tool schema |

Every capability that needs the key returns `unavailable` (not a crash)
when it is missing, and `describe` answers regardless.

`property_intelligence` used to answer without a key by falling back to
what the caller had supplied. It no longer does: the profile is
researched now, so without a key there is nothing honest to return, and
echoing the caller's own input back as a "verified profile" was the
failure mode that removal avoids.

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
  to make, so a multi-call capability cannot overrun the deadline. Omit `deadline_ms` and the agent assumes
  30s.
- **One vendor, via the official SDK.** `clients.py` uses the `anthropic`
  package; `command:` runs under `uv` so the dependency is pinned by
  `uv.lock`. This matches `realty-lead-gen` and `operations-maintenance-agent`,
  and leaves the agent with a single credential.
- **The model researches; the code validates.** Each capability is one
  Claude call with web search enabled, reporting through a tool whose schema
  mirrors that capability's `output_schema`. What stays in code is what is
  not a judgment call: validating the caller's input, deriving price per
  square foot, minting a stable `property_id`. This replaced regex and
  keyword heuristics over search snippets, which read "no oversupply" as
  oversupply and refused any address containing "Bishop" because it
  contains "shop".
- **Evidence into the model is size-capped.** `investment_recommendation`
  rejects caller-supplied evidence objects larger than 8 KiB so the
  prompt cannot grow without bound.
- **Every answer comes back through a tool schema.** Each capability names
  a tool whose schema mirrors what the envelope publishes, so the shape is
  part of what was asked for rather than checked afterwards.
  `tests/golden/` pins the recommendation tool. If the model returns no usable
  `recommendation` or `confidence_percent`, the capability fails
  `unavailable` (retryable) rather than substituting a default — an
  invented "NEGOTIATE at 50%" is indistinguishable from a real call, and
  this is the one field the agent exists to produce.
- **`usage` reports tokens and the model, never money.** Recorded in
  `clients.py` as calls happen and read once in `agent_main.py` where the
  envelope is built, so a request that paid and then failed still reports
  what it spent. See `docs/AGENT_PROTOCOL.md` for why cost is derived rather
  than copied into each agent.

## Tests

```bash
python3 -m unittest discover -s tests
```

`test_agent.py` runs the real `agent_main.py` as a subprocess, the same
way the orchestrator does, so stdout hygiene and the exit code are
checked, not assumed. `test_capabilities.py` covers what the subprocess
tests cannot reach without a key: it stubs `research` where the assertion is
about a capability's wiring, and injects a fake SDK client where it is about
`clients` itself — so usage recording, `pause_turn` resumption and the
error-to-envelope mapping all run for real.

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
