"""Anthropic API client — standard library only, zero third-party imports.

Supports an offline mock mode: if ANTHROPIC_API_KEY starts with "mock-"
the function returns a deterministic response without touching the network.
This keeps tests isolated and fast.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

_DEFAULT_MODEL = "claude-3-5-sonnet-20241022"

_SYSTEM_PROMPT = """You are a healthcare scheduling assistant for a medical clinic.
Help patients book, reschedule, or cancel appointments with empathy and clarity.

Available physicians:
- Dr. Sarah Jenkins (Cardiology)
- Dr. Allison Vance (Pediatrics)
- Dr. Michael Chen (General Medicine)
- Dr. James Carter (Dermatology)

Reply ONLY with a valid JSON object — no prose outside the JSON:
{
  "response": "<professional, empathetic reply to show the patient>",
  "suggested_action": "<one of: list_slots | book_appointment | none>",
  "suggested_action_input": <object with action parameters, or null>
}

Rules:
- suggested_action = "list_slots" → suggested_action_input may include: specialty, doctor_name, date (YYYY-MM-DD)
- suggested_action = "book_appointment" → suggested_action_input MUST include: slot_id, patient_name, patient_phone
- suggested_action = "none" → suggested_action_input = null
- If the patient is missing details needed for booking, ask for them in `response` and use "none".
- Never invent slot IDs; only echo ones the patient provides."""


def _get_model() -> str:
    return os.environ.get("ANTHROPIC_MODEL_CHAT", _DEFAULT_MODEL)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    # Strip markdown fences if the model adds them
    for fence in ("```json", "```"):
        if fence in text:
            text = text.split(fence, 1)[1].split("```", 1)[0].strip()
            break
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)


def call_claude_chat(
    message: str,
    chat_history: list[dict[str, str]] | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Call Anthropic messages API; return (structured_output, usage_dict).

    Raises RuntimeError on HTTP or network errors so the caller can map
    them to the correct agentcall/v1 error type.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    # --- offline mock mode for testing ---
    if api_key.startswith("mock-"):
        return _mock_response(message)

    model = _get_model()
    messages: list[dict[str, str]] = []
    for turn in (chat_history or []):
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": message})

    body = json.dumps({
        "model":      model,
        "max_tokens": 512,
        "system":     _SYSTEM_PROMPT,
        "messages":   messages,
        "temperature": 0.0,
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key":          api_key,
            "anthropic-version":  "2023-06-01",
            "content-type":       "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=25.0) as resp:
            raw = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        try:
            detail = json.loads(detail).get("error", {}).get("message", detail)
        except Exception:
            pass
        raise RuntimeError(f"Anthropic API status {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"Network error reaching Anthropic: {exc}") from exc

    text = "".join(
        b.get("text", "") for b in raw.get("content", []) if b.get("type") == "text"
    )
    usage_raw = raw.get("usage", {})
    inp  = usage_raw.get("input_tokens",  0)
    outp = usage_raw.get("output_tokens", 0)
    rate_in, rate_out = (0.25, 1.25) if "haiku" in model else (3.0, 15.0)
    cost = int((inp / 1_000_000) * rate_in * 1_000_000
               + (outp / 1_000_000) * rate_out * 1_000_000)
    usage: dict[str, int] = {"input_tokens": inp, "output_tokens": outp, "cost_micros": cost}

    return _extract_json(text), usage


# ---------------------------------------------------------------------------
# Deterministic offline responses for unit tests
# ---------------------------------------------------------------------------

def _mock_response(message: str) -> tuple[dict[str, Any], dict[str, int]]:
    msg = message.lower()
    if "cardiology" in msg or "heart" in msg:
        out = {
            "response": "I can check Cardiology slots for Dr. Sarah Jenkins.",
            "suggested_action": "list_slots",
            "suggested_action_input": {"specialty": "Cardiology"},
        }
    elif "dermatology" in msg or "skin" in msg:
        out = {
            "response": "Let me look up Dermatology slots for you.",
            "suggested_action": "list_slots",
            "suggested_action_input": {"specialty": "Dermatology"},
        }
    elif "book" in msg and "slot_" in msg:
        parts = message.split()
        slot_id = next((p for p in parts if p.startswith("slot_")), "slot_doc-1_2026-08-02_1")
        out = {
            "response": f"Booking slot {slot_id} for you now.",
            "suggested_action": "book_appointment",
            "suggested_action_input": {
                "slot_id": slot_id,
                "patient_name": "Test Patient",
                "patient_phone": "555-0100",
            },
        }
    else:
        out = {
            "response": "Hello! I am your appointment assistant. How can I help you today?",
            "suggested_action": "none",
            "suggested_action_input": None,
        }
    usage: dict[str, int] = {"input_tokens": 12, "output_tokens": 8, "cost_micros": 50}
    return out, usage
