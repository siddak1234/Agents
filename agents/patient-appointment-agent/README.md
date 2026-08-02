# patient-appointment-agent

An administrative healthcare AI assistant that automates the full appointment scheduling lifecycle for a medical clinic. Patients can query physician availability, book consultations, and cancel bookings. It also understands free-form natural language requests and translates them into structured scheduling actions.

It does **not** store personal health records, provide medical advice, or run any persistent service.

---

## Capabilities

| Capability | What it does |
|---|---|
| `describe` | Returns the agent name, protocol, and capability list. Costs nothing. |
| `list_slots` | Returns open slots for the next 7 days, filtered by specialty, physician name, or date (YYYY-MM-DD). |
| `book_appointment` | Reserves a slot by `slot_id`, `patient_name`, and `patient_phone`. |
| `cancel_appointment` | Cancels a booking by `appointment_id`. The slot becomes bookable again immediately. |
| `chat` | Accepts a free-form patient message and returns a reply plus a structured suggested action. Requires `ANTHROPIC_API_KEY`. |

---

## Environment variables

| Variable | Required by | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | `chat` | Anthropic API credential. If absent, `chat` returns `unavailable`. |
| `ANTHROPIC_MODEL_CHAT` | `chat` (optional) | Override the default model (`claude-3-5-sonnet-20241022`). |

All other capabilities work with **no credentials**.

---

## Running locally

```bash
# Describe the agent
echo '{"protocol":"agentcall/v1","capability":"describe","input":{}}' | python3 agent_main.py

# Check open slots
echo '{"protocol":"agentcall/v1","capability":"list_slots","input":{"specialty":"Cardiology"}}' | python3 agent_main.py

# Book an appointment
echo '{"protocol":"agentcall/v1","capability":"book_appointment","input":{"slot_id":"slot_doc-1_2026-08-03_1","patient_name":"Jane Doe","patient_phone":"555-1234"}}' | python3 agent_main.py
```

## Running tests

```bash
python3 -m unittest discover -s tests -v
```

---

## What it will not do

- It will not give medical advice or triage symptoms.
- It will not send email or SMS confirmations (that belongs to a notification service).
- It will not expose a web API or run as a background daemon.
