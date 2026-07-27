"""Starter tests for the template agent.

    python3 -m unittest discover -s tests -v

Standard library only, like the agent itself — no install step, so these run
the moment you copy the folder. Swap in pytest if your agent grows real
dependencies; `realty-lead-gen/tests/` is the worked example.

Each test calls the agent the way the orchestrator does: a fresh process, one
JSON request on stdin, one envelope expected on stdout. That is deliberate.
Testing `dispatch()` directly would pass even if the agent leaked a log line
to stdout, which is the failure most likely to reach production.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
ENTRYPOINT = AGENT_DIR / "agent_main.py"
PROTOCOL = "agentcall/v1"


def call(capability: str, payload: dict | None = None) -> dict:
    """Invoke the agent as a subprocess and return its envelope."""
    request = json.dumps({"protocol": PROTOCOL, "capability": capability, "input": payload or {}})
    proc = subprocess.run(
        [sys.executable, str(ENTRYPOINT)],
        input=request,
        capture_output=True,
        text=True,
        cwd=AGENT_DIR,
        timeout=30,
        check=False,
    )
    # Exit 0 even for failures: an envelope was produced. See AGENT_PROTOCOL.md.
    assert proc.returncode == 0, f"agent exited {proc.returncode}\n{proc.stderr}"
    # stdout must be exactly one JSON object — a stray print would break this.
    return json.loads(proc.stdout)


class TestContract(unittest.TestCase):
    """These hold for every agent. Keep them when you replace the rest."""

    def test_describe_reports_name_and_capabilities(self) -> None:
        envelope = call("describe")
        self.assertTrue(envelope["ok"], envelope["error"])
        self.assertIn("name", envelope["output"])
        self.assertTrue(envelope["output"]["capabilities"])

    def test_unknown_capability_is_a_typed_error(self) -> None:
        envelope = call("no-such-capability")
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_every_response_reports_usage(self) -> None:
        self.assertIn("usage", call("describe"))


class TestGreet(unittest.TestCase):
    """TODO(new agent): delete this class and test your own capability."""

    def test_greets_by_name(self) -> None:
        envelope = call("greet", {"name": "Ada"})
        self.assertEqual(envelope["output"]["greeting"], "Hello, Ada!")

    def test_rejects_empty_name(self) -> None:
        envelope = call("greet", {"name": "   "})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")


if __name__ == "__main__":
    unittest.main()
