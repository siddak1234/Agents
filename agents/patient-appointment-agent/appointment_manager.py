"""Scheduling logic for patient-appointment-agent.

Owns the knowledge of physicians, time slots, and bookings.
Imports nothing from the agentcall protocol — it is plain Python.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Static data — physicians this clinic employs
# ---------------------------------------------------------------------------

DOCTORS: list[dict[str, Any]] = [
    {"doctor_id": "doc-1", "name": "Dr. Sarah Jenkins",  "specialty": "Cardiology"},
    {"doctor_id": "doc-2", "name": "Dr. Allison Vance",  "specialty": "Pediatrics"},
    {"doctor_id": "doc-3", "name": "Dr. Michael Chen",   "specialty": "General Medicine"},
    {"doctor_id": "doc-4", "name": "Dr. James Carter",   "specialty": "Dermatology"},
]

# Fixed daily time slots per physician (label → "HH:MM")
_DAILY_TIMES: dict[str, str] = {
    "1": "09:00",
    "2": "11:00",
    "3": "14:00",
    "4": "16:00",
}

_LOOKAHEAD_DAYS = 7   # how many days ahead to generate slots


# ---------------------------------------------------------------------------
# Persistence — a flat JSON file in the agent's own folder
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    """Always resolved relative to this file so tests keep their own DB."""
    return Path(__file__).resolve().parent / "appointments_db.json"


def _load_appointments() -> list[dict[str, Any]]:
    path = _db_path()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_appointments(appointments: list[dict[str, Any]]) -> None:
    _db_path().write_text(json.dumps(appointments, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Slot generation — dynamic so tests never rot on a hard-coded date
# ---------------------------------------------------------------------------

def _generate_slots() -> list[dict[str, Any]]:
    """All slots for the next _LOOKAHEAD_DAYS days, before filtering booked ones."""
    today = datetime.date.today()
    slots: list[dict[str, Any]] = []
    for offset in range(_LOOKAHEAD_DAYS):
        day = (today + datetime.timedelta(days=offset)).isoformat()
        for doctor in DOCTORS:
            for index, time_str in _DAILY_TIMES.items():
                slots.append({
                    "slot_id":    f"slot_{doctor['doctor_id']}_{day}_{index}",
                    "doctor_id":  doctor["doctor_id"],
                    "doctor_name": doctor["name"],
                    "specialty":  doctor["specialty"],
                    "date":       day,
                    "time":       time_str,
                })
    return slots


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def list_available_slots(
    *,
    specialty: str | None = None,
    doctor_name: str | None = None,
    date: str | None = None,
) -> list[dict[str, Any]]:
    """Return slots that are not booked, optionally filtered."""
    booked = {
        a["slot_id"]
        for a in _load_appointments()
        if a.get("status") == "confirmed"
    }

    result = []
    for slot in _generate_slots():
        if slot["slot_id"] in booked:
            continue
        if specialty and specialty.strip().lower() != slot["specialty"].lower():
            continue
        if doctor_name and doctor_name.strip().lower() not in slot["doctor_name"].lower():
            continue
        if date and date.strip() != slot["date"]:
            continue
        result.append(slot)
    return result


def book_appointment(
    *,
    slot_id: str,
    patient_name: str,
    patient_phone: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Book the slot; raise ValueError if it doesn't exist or is already taken."""
    all_slots = _generate_slots()
    slot = next((s for s in all_slots if s["slot_id"] == slot_id), None)
    if slot is None:
        raise ValueError(f"Slot '{slot_id}' does not exist in the next {_LOOKAHEAD_DAYS} days.")

    appointments = _load_appointments()
    for appt in appointments:
        if appt["slot_id"] == slot_id and appt.get("status") == "confirmed":
            raise ValueError(f"Slot '{slot_id}' is already booked.")

    now = datetime.datetime.now(datetime.timezone.utc)
    appt_id = f"appt_{int(now.timestamp())}_{slot_id}"
    new_appt: dict[str, Any] = {
        "appointment_id": appt_id,
        "slot_id":        slot_id,
        "patient_name":   patient_name,
        "patient_phone":  patient_phone,
        "doctor_id":      slot["doctor_id"],
        "doctor_name":    slot["doctor_name"],
        "specialty":      slot["specialty"],
        "date":           slot["date"],
        "time":           slot["time"],
        "reason":         reason or "",
        "status":         "confirmed",
        "created_at":     now.isoformat(),
    }
    appointments.append(new_appt)
    _save_appointments(appointments)
    return new_appt


def cancel_appointment(appointment_id: str) -> dict[str, Any]:
    """Mark an appointment cancelled; raise ValueError if not found or already cancelled."""
    appointments = _load_appointments()
    for appt in appointments:
        if appt["appointment_id"] == appointment_id:
            if appt["status"] == "cancelled":
                raise ValueError(f"Appointment '{appointment_id}' is already cancelled.")
            appt["status"] = "cancelled"
            appt["cancelled_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            _save_appointments(appointments)
            return appt
    raise ValueError(f"Appointment '{appointment_id}' not found.")
