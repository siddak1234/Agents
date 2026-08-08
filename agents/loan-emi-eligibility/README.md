# loan-emi-eligibility

Computes loan EMI and checks a borrower's eligibility against a lender's
**FOIR (Fixed Obligation to Income Ratio)** policy table. Built for loan
origination / underwriting callers who need a standardized eligibility
decision and a maximum eligible loan amount, without hand-coding FOIR bands
themselves.

## What it does

- **`calculate_emi`** — standard reducing-balance EMI formula. Given
  principal, annual interest rate, and tenure in months, returns the monthly
  EMI, total payment, and total interest.
- **`check_eligibility`** — given an applicant's monthly income, existing
  EMI obligations, and a requested loan, computes the FOIR the new loan
  would create and compares it against the limit for the applicant's tier.
  Returns whether they're eligible and, if not, the maximum principal they
  *would* qualify for at the same rate and tenure.

### The FOIR rulebook this agent owns

| Tier                | FOIR limit |
| -------------------- | ---------- |
| `standard`            | 50%        |
| `salaried_premium`    | 55%        |
| `self_employed`       | 40%        |

`applicant_tier` defaults to `standard` if omitted.

## What it will NOT do

- It does not check creditworthiness, credit score, or CIBIL history.
- It does not make a final lending decision — FOIR is one input among many
  a real underwriting process uses.
- It does not persist applications, store PII, or call any external service.
  It is a pure, deterministic calculation with no network access and no
  credentials.

## Running it

```
echo '{"protocol":"agentcall/v1","capability":"check_eligibility","input":{"monthly_income":100000,"existing_emis":5000,"requested_principal":200000,"annual_rate_percent":10,"tenure_months":24,"applicant_tier":"standard"},"request_id":"","deadline_ms":5000}' | python3 agent_main.py
```

## Errors

All failures this agent can return are `invalid_request` — every input is
validated locally with no external dependency, so nothing here can be
`unavailable`, and there's no long-running work that could `timeout`.
Examples: a negative or zero principal, a non-numeric field, a missing
required field, or an unrecognized `applicant_tier`.

## Tests

```
python3 -m unittest discover -s tests
```

Tests call `agent_main.py` as a subprocess (the way the orchestrator does),
covering `describe`, both capabilities' happy paths, each validation error,
an unknown capability, a wrong protocol, and malformed stdin. No test
touches the network — this agent has no credentials or external calls to
make.
