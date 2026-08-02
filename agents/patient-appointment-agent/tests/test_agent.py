"""Tests for patient-appointment-agent.

Every test drives the real entrypoint as a subprocess — the same way the
orchestrator does — so stdout hygiene, exit codes, and JSON parsing are all
exercised rather than assumed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

AGENT_DIR  = Path(__file__).resolve().parent.parent
ENTRYPOINT = AGENT_DIR / "agent_main.py"
PROTOCOL   = "agentcall/v1"
DB_FILE    = AGENT_DIR / "appointments_db.json"


def call(
    capability: str,
    payload: dict | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict:
    """Run the agent in a subprocess and return the parsed envelope."""
    env = os.environ.copy()
    # Strip real API keys so tests are hermetic by default
    env.pop("ANTHROPIC_API_KEY", None)
    if extra_env:
        env.update(extra_env)

    request = json.dumps({
        "protocol":   PROTOCOL,
        "capability": capability,
        "input":      payload or {},
    })
    proc = subprocess.run(
        [sys.executable, str(ENTRYPOINT)],
        input=request,
        capture_output=True,
        text=True,
        cwd=AGENT_DIR,
        env=env,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, (
        f"agent exited {proc.returncode}\nstderr: {proc.stderr}"
    )
    envelope = json.loads(proc.stdout)   # fails if anything else leaked to stdout
    return envelope


class TestDescribe(unittest.TestCase):
    def test_describe_returns_all_capabilities(self) -> None:
        env = call("describe")
        self.assertTrue(env["ok"])
        out = env["output"]
        self.assertEqual(out["name"], "patient-appointment-agent")
        self.assertEqual(out["protocol"], PROTOCOL)
        names = {c["name"] for c in out["capabilities"]}
        self.assertIn("describe",           names)
        self.assertIn("list_slots",         names)
        self.assertIn("book_appointment",   names)
        self.assertIn("cancel_appointment", names)
        self.assertIn("chat",               names)

    def test_unknown_capability_returns_invalid_request(self) -> None:
        env = call("fly_to_moon")
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["type"], "invalid_request")
        self.assertEqual(env["error"]["retryable"], False)


class TestListSlots(unittest.TestCase):
    def test_returns_slots_for_next_seven_days(self) -> None:
        env = call("list_slots")
        self.assertTrue(env["ok"])
        self.assertIsInstance(env["output"]["slots"], list)
        self.assertGreater(len(env["output"]["slots"]), 0)

    def test_specialty_filter_works(self) -> None:
        env = call("list_slots", {"specialty": "Cardiology"})
        self.assertTrue(env["ok"])
        for slot in env["output"]["slots"]:
            self.assertEqual(slot["specialty"], "Cardiology")

    def test_invalid_date_format_returns_error(self) -> None:
        env = call("list_slots", {"date": "02-08-2026"})
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["type"], "invalid_request")
        self.assertIn("YYYY-MM-DD", env["error"]["message"])


class TestBookAndCancel(unittest.TestCase):
    def setUp(self) -> None:
        if DB_FILE.exists():
            DB_FILE.unlink()

    def tearDown(self) -> None:
        if DB_FILE.exists():
            DB_FILE.unlink()

    def test_book_then_slot_disappears_then_cancel_restores_it(self) -> None:
        # 1. pick the first available Pediatrics slot
        slots_env = call("list_slots", {"specialty": "Pediatrics"})
        self.assertTrue(slots_env["ok"])
        slots = slots_env["output"]["slots"]
        self.assertGreater(len(slots), 0)
        slot_id = slots[0]["slot_id"]

        # 2. book it
        book_env = call("book_appointment", {
            "slot_id":      slot_id,
            "patient_name": "Jane Doe",
            "patient_phone": "555-1234",
            "reason":       "Annual check-up",
        })
        self.assertTrue(book_env["ok"], book_env.get("error"))
        appt = book_env["output"]
        self.assertEqual(appt["status"], "confirmed")
        self.assertEqual(appt["patient_name"], "Jane Doe")
        appt_id = appt["appointment_id"]

        # 3. slot must no longer appear
        after = call("list_slots", {"specialty": "Pediatrics"})
        taken = {s["slot_id"] for s in after["output"]["slots"]}
        self.assertNotIn(slot_id, taken)

        # 4. double-book must fail
        dbl = call("book_appointment", {
            "slot_id": slot_id, "patient_name": "Bob", "patient_phone": "000"
        })
        self.assertFalse(dbl["ok"])
        self.assertEqual(dbl["error"]["type"], "invalid_request")
        self.assertIn("already booked", dbl["error"]["message"])

        # 5. cancel and slot reappears
        cancel_env = call("cancel_appointment", {"appointment_id": appt_id})
        self.assertTrue(cancel_env["ok"])
        self.assertEqual(cancel_env["output"]["status"], "cancelled")

        restored = call("list_slots", {"specialty": "Pediatrics"})
        restored_ids = {s["slot_id"] for s in restored["output"]["slots"]}
        self.assertIn(slot_id, restored_ids)

    def test_missing_required_field_returns_invalid_request(self) -> None:
        env = call("book_appointment", {
            "slot_id": "slot_doc-1_2026-08-02_1",
            "patient_name": "Alice",
            # patient_phone intentionally missing
        })
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["type"], "invalid_request")
        self.assertIn("patient_phone", env["error"]["message"])

    def test_cancel_nonexistent_appointment_returns_error(self) -> None:
        env = call("cancel_appointment", {"appointment_id": "appt_does_not_exist"})
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["type"], "invalid_request")


class TestChat(unittest.TestCase):
    MOCK_ENV = {"ANTHROPIC_API_KEY": "mock-test-key"}

    def test_chat_without_key_returns_unavailable(self) -> None:
        env = call("chat", {"message": "I need an appointment"})
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["type"], "unavailable")
        self.assertEqual(env["error"]["retryable"], False)

    def test_chat_mock_general_query(self) -> None:
        env = call("chat", {"message": "Hello"}, extra_env=self.MOCK_ENV)
        self.assertTrue(env["ok"], env.get("error"))
        self.assertEqual(env["output"]["suggested_action"], "none")
        self.assertIn("assistant", env["output"]["response"].lower())

    def test_chat_mock_specialty_triggers_list_slots(self) -> None:
        env = call("chat", {"message": "I need a cardiology slot"},
                   extra_env=self.MOCK_ENV)
        self.assertTrue(env["ok"])
        self.assertEqual(env["output"]["suggested_action"], "list_slots")
        self.assertEqual(
            env["output"]["suggested_action_input"]["specialty"], "Cardiology"
        )

    def test_chat_missing_message_returns_invalid_request(self) -> None:
        env = call("chat", {}, extra_env=self.MOCK_ENV)
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["type"], "invalid_request")
        self.assertIn("message", env["error"]["message"])


if __name__ == "__main__":
    unittest.main()
