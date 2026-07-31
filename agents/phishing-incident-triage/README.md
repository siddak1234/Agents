# phishing-incident-triage

A deterministic phishing-risk assessment agent. Analyses a suspicious email
and returns a structured report with a 0-100 risk score, severity
classification, extracted indicators, and recommended response actions.

**No API keys, no network calls, no model invocations.**  Every result is
produced through pure rule-based logic — URL analysis, sender-reputation
heuristics, authentication-result scoring, social-engineering pattern
detection, and a composite risk rubric.  Deterministic and auditable.

## When to use this

Security analysts, IT teams, and small organisations that need consistent
first-level phishing triage — either as a standalone tool or as part of a
larger incident-response pipeline.

## Capabilities

| Name | Description |
|---|---|
| `triage_email` | Analyse a suspicious email and return a structured phishing-risk assessment. |

## Run it now

```bash
echo '{
  "protocol": "agentcall/v1",
  "capability": "triage_email",
  "input": {
    "subject": "URGENT: Your account has been compromised",
    "sender": "security@paypa1-verify.tk",
    "body": "Dear customer, your account will be suspended within 24 hours. Click here to verify your identity immediately.",
    "urls": ["http://192.168.1.1/verify?user=victim@corp.com"],
    "attachments": ["invoice.pdf.exe"]
  }
}' | python3 agent_main.py
```

## What it does

1. **URL analysis** — flags IP-hosted URLs, shorteners, suspicious TLDs,
   punycode domains, credential-injection patterns, and excessive links.
2. **Sender analysis** — detects free-email providers, reply-to mismatches,
   display-name spoofing, and random local parts.
3. **Authentication analysis** — evaluates SPF, DKIM, DMARC results and
   scores failures and missing results.
4. **Content analysis** — scans for urgency keywords, social-engineering
   techniques (authority impersonation, urgency/fear, greed, curiosity bait,
   pretexting), credential-harvesting requests, embedded forms, and
   obfuscation.
5. **Attachment analysis** — flags dangerous executable types, double
   extensions, and archive files that may contain payloads.

Each area contributes a weighted score to a composite 0-100 risk score,
mapped to `low`, `medium`, `high`, or `critical` severity.

## What it will NOT do

- Generate phishing content, credential-harvesting pages, or malware.
- Make network requests or call external APIs.
- Store, cache, or transmit any data.
- Replace a full email-security gateway — this is first-level triage.

## Architecture

| File | Role |
|---|---|
| `agent_main.py` | Protocol adapter — translates `agentcall/v1` wire format. No business logic. |
| `phishing_triage.py` | Core analysis engine — all detection logic, scoring, and knowledge tables. |
| `tests/` | Tests that invoke the agent as a subprocess, covering every error type. |

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## License

MIT — see [LICENSE](LICENSE).
