"""Tests for commercial-lease-agent, run as the orchestrator would call it."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
AGENT_MAIN = AGENT_DIR / "agent_main.py"

sys.path.insert(0, str(AGENT_DIR))

import agent_main  # noqa: E402 — needs AGENT_DIR on sys.path first


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


def test_usage_names_the_model_never_a_price():
    # docs/AGENT_PROTOCOL.md: usage carries tokens and the model it called.
    # An agent still emitting cost_micros decodes as model: null, silently
    # claiming no model was called — the gates would not notice.
    usage = _call("calculate_deadline", {"lease_end_date": "2028-12-31", "notice_period_days": 90})["usage"]
    assert usage == {"input_tokens": 0, "output_tokens": 0, "model": None}
    assert "cost_micros" not in usage


def test_calculate_deadline_unit_mix_up_is_invalid_request_not_internal():
    # 90 days passed in seconds. This used to escape as OverflowError and be
    # reported `internal` with the capability name lost from the envelope.
    envelope = _call(
        "calculate_deadline",
        {"lease_end_date": "2026-01-01", "notice_period_days": 90 * 24 * 60 * 60},
    )
    assert envelope["ok"] is False
    assert envelope["capability"] == "calculate_deadline"
    assert envelope["error"]["type"] == "invalid_request"
    assert envelope["error"]["retryable"] is False


def test_extract_clauses_refuses_a_document_over_the_page_cap():
    # Each chunk of pages is one billed call, so the ceiling is refused before
    # any of them are made.
    envelope = _call("extract_clauses", {"lease_pages": ["page"] * 1001})
    assert envelope["ok"] is False
    assert envelope["error"]["type"] == "invalid_request"
    assert "1000" in envelope["error"]["message"]


def test_extract_clauses_success_envelope(monkeypatch):
    """The success path through agent_main, which no subprocess test can reach.

    Mirrors realty-lead-gen's test_agentcall happy-path test: it is the only
    thing that would catch a wrong output key, dropped usage, or a broken
    _EXTRACT_ERROR_MAP entry.
    """
    quote = "Tenant may renew for one additional term of five years."

    class _Usage:
        input_tokens, output_tokens = 120, 45

    class _Block:
        type, name = "tool_use", "record_clauses"

        def __init__(self):
            self.input = {"clauses": [{
                "clause_type": "renewal", "text_quote": quote,
                "page_number": 1, "confidence_score": 0.93,
            }]}

    class _Response:
        content, usage = [_Block()], _Usage()

    class _FakeAnthropic:
        def Anthropic(self, api_key):  # noqa: N802 — matches anthropic.Anthropic
            return type("C", (), {"messages": type("M", (), {"create": lambda s, **k: _Response()})()})()

    monkeypatch.setitem(sys.modules, "anthropic", _FakeAnthropic())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-testing-only")

    envelope = agent_main.dispatch(json.dumps({
        "protocol": "agentcall/v1", "capability": "extract_clauses",
        "input": {"lease_pages": [quote]}, "request_id": "", "deadline_ms": 120000,
    }))

    assert envelope["ok"] is True
    assert envelope["capability"] == "extract_clauses"
    assert envelope["output"]["clauses"] == [{
        "clause_type": "renewal", "text_quote": quote,
        "page_number": 1, "confidence_score": 0.93,
    }]
    assert envelope["usage"] == {
        "input_tokens": 120, "output_tokens": 45, "model": "claude-sonnet-5",
    }
    assert envelope["error"] is None
