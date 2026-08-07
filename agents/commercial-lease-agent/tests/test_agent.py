"""Tests for commercial-lease-agent, run as the orchestrator would call it."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
AGENT_MAIN = AGENT_DIR / "agent_main.py"


def _call(capability: str, input_payload: dict, env: dict | None = None) -> dict:
    request = {
        "protocol": "agentcall/v1",
        "capability": capability,
        "input": input_payload,
        "request_id": "",
        "deadline_ms": 120000,
    }
    proc = subprocess.run(
        [sys.executable, str(AGENT_MAIN)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        cwd=AGENT_DIR,
        env=env if env is not None else os.environ.copy(),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_describe_matches_manifest():
    envelope = _call("describe", {})
    assert envelope["ok"] is True
    names = {c["name"] for c in envelope["output"]["capabilities"]}
    assert names == {"describe", "extract_clauses", "calculate_deadline"}


def test_unknown_capability_is_invalid_request():
    envelope = _call("not_a_real_capability", {})
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "invalid_request"


def test_calculate_deadline_success():
    envelope = _call("calculate_deadline", {"lease_end_date": "2028-12-31", "notice_period_days": 90})
    assert envelope["ok"] is True
    assert envelope["output"]["deadline_date"] == "2028-10-02"


def test_calculate_deadline_bad_date_is_invalid_request():
    envelope = _call("calculate_deadline", {"lease_end_date": "not-a-date", "notice_period_days": 90})
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "invalid_request"


def test_calculate_deadline_negative_notice_period_is_invalid_request():
    envelope = _call("calculate_deadline", {"lease_end_date": "2028-12-31", "notice_period_days": -5})
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "invalid_request"


def test_extract_clauses_missing_pages_is_invalid_request():
    envelope = _call("extract_clauses", {})
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "invalid_request"


def test_extract_clauses_missing_credential_is_unavailable():
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    envelope = _call("extract_clauses", {"lease_pages": ["some lease text"]}, env=env)
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "unavailable"
