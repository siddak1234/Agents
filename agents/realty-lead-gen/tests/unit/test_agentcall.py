"""Unit tests for the agentcall/v1 adapter.

The adapter is the only code the repository orchestrator executes, so its
envelopes are contract, not detail: every malformed request must come back
as a well-formed `invalid_request` envelope, never a traceback, and stdout
must carry the envelope and nothing else.

The grading capability is exercised through fakes patched in at the lazy
import seam — `_grade_photos` imports its collaborators at call time, so
`monkeypatch.setattr` on the source modules is enough. No network, no keys.
"""

from __future__ import annotations

import io
import json
import sys
from types import SimpleNamespace
from typing import Any

import pytest

import realty_lead_gen.agents.claude_client as claude_client_module
import realty_lead_gen.agents.photo_grader as photo_grader_module
import realty_lead_gen.config as config_module
from realty_lead_gen import agentcall
from realty_lead_gen.utils.retry import TransientError


def _request(**overrides: Any) -> str:
    request: dict[str, Any] = {
        "protocol": agentcall.PROTOCOL,
        "capability": "describe",
        "input": {},
    }
    request.update(overrides)
    return json.dumps(request)


def _fake_result() -> SimpleNamespace:
    return SimpleNamespace(
        overall_condition="C3",
        overall_confidence=0.9,
        rehab_total_low_cents=1_200_000,
        rehab_total_high_cents=3_400_000,
        systems={"roof": "C3"},
        red_flags=["water staining"],
        notes_for_reviewer="ok",
        usage=SimpleNamespace(
            model="claude-test",
            input_tokens=11,
            output_tokens=7,
        ),
    )


def _patch_grading(
    monkeypatch: pytest.MonkeyPatch,
    *,
    available: bool = True,
    error: Exception | None = None,
) -> None:
    class FakeClient:
        def __init__(self, settings: Any) -> None:
            self.available = available

    class FakeGrader:
        def __init__(self, claude: Any, settings: Any) -> None:
            pass

        async def grade(self, urls: list[str], *, market_hint: str | None = None) -> Any:
            if error is not None:
                raise error
            return _fake_result()

    # A bare SimpleNamespace was enough while nothing read a settings field.
    # `_grade_photos` now configures logging from `app_log_level` /
    # `app_log_format`, so the stub has to carry the fields the real Settings
    # has — a stub thinner than the type it stands in for passes for the wrong
    # reason.
    monkeypatch.setattr(
        config_module,
        "get_settings",
        lambda: SimpleNamespace(app_log_level="INFO", app_log_format="json"),
    )
    monkeypatch.setattr(claude_client_module, "ClaudeClient", FakeClient)
    monkeypatch.setattr(photo_grader_module, "PhotoGrader", FakeGrader)


@pytest.mark.unit
class TestDispatchValidation:
    def test_rejects_invalid_json(self) -> None:
        envelope = agentcall._dispatch("{not json")
        assert envelope["ok"] is False
        assert envelope["error"]["type"] == "invalid_request"
        assert "not valid JSON" in envelope["error"]["message"]

    def test_rejects_non_object_request(self) -> None:
        envelope = agentcall._dispatch(json.dumps([1, 2]))
        assert envelope["ok"] is False
        assert envelope["error"]["message"] == "request must be a JSON object"

    def test_empty_stdin_fails_the_protocol_check(self) -> None:
        envelope = agentcall._dispatch("")
        assert envelope["ok"] is False
        assert "unsupported protocol" in envelope["error"]["message"]

    def test_rejects_wrong_protocol(self) -> None:
        envelope = agentcall._dispatch(_request(protocol="agentcall/v2"))
        assert envelope["ok"] is False
        assert "agentcall/v2" in envelope["error"]["message"]

    def test_rejects_missing_capability(self) -> None:
        request = json.loads(_request())
        del request["capability"]
        envelope = agentcall._dispatch(json.dumps(request))
        assert envelope["ok"] is False
        assert envelope["error"]["message"] == "missing 'capability'"

    def test_rejects_non_object_input(self) -> None:
        envelope = agentcall._dispatch(_request(input=[1]))
        assert envelope["ok"] is False
        assert envelope["error"]["message"] == "'input' must be an object"
        assert envelope["capability"] == "describe"

    def test_null_input_defaults_to_empty_object(self) -> None:
        envelope = agentcall._dispatch(_request(input=None))
        assert envelope["ok"] is True

    def test_unknown_capability_lists_what_is_offered(self) -> None:
        envelope = agentcall._dispatch(_request(capability="mow_lawn"))
        assert envelope["ok"] is False
        assert "describe" in envelope["error"]["message"]
        assert "grade_photos" in envelope["error"]["message"]


@pytest.mark.unit
class TestDescribe:
    def test_envelope_mirrors_declared_capabilities(self) -> None:
        envelope = agentcall._dispatch(_request(capability="describe"))
        assert envelope["ok"] is True
        assert envelope["error"] is None
        assert envelope["output"]["name"] == agentcall.AGENT_NAME
        assert envelope["output"]["protocol"] == agentcall.PROTOCOL
        declared = [(c["name"], c["description"]) for c in envelope["output"]["capabilities"]]
        assert declared == list(agentcall.CAPABILITIES)

    def test_usage_is_zero(self) -> None:
        envelope = agentcall._dispatch(_request(capability="describe"))
        assert envelope["usage"] == {"input_tokens": 0, "output_tokens": 0, "model": None}


@pytest.mark.unit
class TestGradePhotosValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"photo_urls": []},
            {"photo_urls": "not-a-list"},
            {"photo_urls": [1, 2]},
            {"photo_urls": [""]},
        ],
    )
    def test_rejects_bad_photo_urls(self, payload: dict[str, Any]) -> None:
        envelope = agentcall._dispatch(_request(capability="grade_photos", input=payload))
        assert envelope["ok"] is False
        assert envelope["capability"] == "grade_photos"
        assert "'photo_urls'" in envelope["error"]["message"]

    def test_rejects_non_string_market_hint(self) -> None:
        payload = {"photo_urls": ["https://x/1.jpg"], "market_hint": 7}
        envelope = agentcall._dispatch(_request(capability="grade_photos", input=payload))
        assert envelope["ok"] is False
        assert "'market_hint'" in envelope["error"]["message"]


@pytest.mark.unit
class TestGradePhotosExecution:
    def test_missing_key_disables_not_crashes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_grading(monkeypatch, available=False)
        payload = {"photo_urls": ["https://x/1.jpg"]}
        envelope = agentcall._dispatch(_request(capability="grade_photos", input=payload))
        assert envelope["ok"] is False
        assert envelope["error"]["type"] == "unavailable"
        assert envelope["error"]["retryable"] is False
        assert "ANTHROPIC_API_KEY" in envelope["error"]["message"]

    def test_happy_path_translates_result_and_usage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_grading(monkeypatch)
        payload = {"photo_urls": ["https://x/1.jpg"], "market_hint": "78701"}
        envelope = agentcall._dispatch(_request(capability="grade_photos", input=payload))
        assert envelope["ok"] is True
        assert envelope["output"]["overall_condition"] == "C3"
        assert envelope["output"]["rehab_total_low_cents"] == 1_200_000
        assert envelope["output"]["model"] == "claude-test"
        assert envelope["usage"] == {
            "input_tokens": 11,
            "output_tokens": 7,
            "model": "claude-test",
        }

    def test_grader_crash_becomes_typed_internal_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_grading(monkeypatch, error=RuntimeError("boom"))
        payload = {"photo_urls": ["https://x/1.jpg"]}
        envelope = agentcall._dispatch(_request(capability="grade_photos", input=payload))
        assert envelope["ok"] is False
        assert envelope["error"]["type"] == "internal"
        assert envelope["error"]["message"] == "RuntimeError: boom"


@pytest.mark.unit
class TestMain:
    def _run_main(self, monkeypatch: pytest.MonkeyPatch, stdin: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        monkeypatch.setattr(sys, "stderr", err)
        monkeypatch.setattr(sys, "stdout", out)
        code = agentcall.main()
        return code, out.getvalue(), err.getvalue()

    def test_writes_exactly_one_envelope_line(self, monkeypatch: pytest.MonkeyPatch) -> None:
        code, out, _ = self._run_main(monkeypatch, _request())
        assert code == 0
        assert out.endswith("\n")
        envelope = json.loads(out)
        assert envelope["ok"] is True

    def test_stray_prints_land_on_stderr_not_stdout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_dispatch = agentcall._dispatch

        def noisy_dispatch(raw: str) -> dict[str, Any]:
            print("stray log line")  # the stray output this test exists to catch
            return real_dispatch(raw)

        monkeypatch.setattr(agentcall, "_dispatch", noisy_dispatch)
        code, out, err = self._run_main(monkeypatch, _request())
        assert code == 0
        json.loads(out)  # stdout is still exactly one parseable envelope
        assert "stray log line" in err

    def test_dispatch_crash_still_produces_an_envelope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def exploding_dispatch(raw: str) -> dict[str, Any]:
            raise ValueError("kaboom")

        monkeypatch.setattr(agentcall, "_dispatch", exploding_dispatch)
        code, out, err = self._run_main(monkeypatch, _request())
        assert code == 0
        envelope = json.loads(out)
        assert envelope["ok"] is False
        assert envelope["error"]["type"] == "internal"
        assert envelope["error"]["message"] == "ValueError: kaboom"
        assert "Traceback" in err

    def test_stdout_is_restored_after_main(self, monkeypatch: pytest.MonkeyPatch) -> None:
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdin", io.StringIO(_request()))
        monkeypatch.setattr(sys, "stdout", out)
        agentcall.main()
        assert sys.stdout is out


@pytest.mark.unit
class TestTransientFailuresStayRetryable:
    """`claude_client` classifies transient vs permanent; the adapter dropped it.

    Every failure became `internal` / `retryable: false`, so a caller reading
    the flag off docs/INTERN_BRIEF.md's table was told a retry could not help
    after a rate limit or a 5xx — the one case where it can.
    """

    def test_a_transient_failure_is_unavailable_and_retryable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_grading(monkeypatch, error=TransientError("rate limited"))
        envelope = agentcall._dispatch(
            _request(capability="grade_photos", input={"photo_urls": ["https://x/1.jpg"]})
        )
        assert envelope["ok"] is False
        assert envelope["error"]["type"] == "unavailable"
        assert envelope["error"]["retryable"] is True

    def test_a_transient_timeout_is_reported_as_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exc = TransientError("request timed out")
        exc.__cause__ = TimeoutError("deadline")
        _patch_grading(monkeypatch, error=exc)
        envelope = agentcall._dispatch(
            _request(capability="grade_photos", input={"photo_urls": ["https://x/1.jpg"]})
        )
        assert envelope["error"]["type"] == "timeout"
        assert envelope["error"]["retryable"] is True

    def test_a_permanent_failure_is_still_internal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_grading(monkeypatch, error=RuntimeError("boom"))
        envelope = agentcall._dispatch(
            _request(capability="grade_photos", input={"photo_urls": ["https://x/1.jpg"]})
        )
        assert envelope["error"]["type"] == "internal"
        assert envelope["error"]["retryable"] is False


@pytest.mark.unit
class TestNestedRepairCostsAreCents:
    """One envelope carried `rehab_total_low_cents` beside a float `cost_low_usd`."""

    def test_repair_items_are_converted_to_integer_cents(self) -> None:
        out = photo_grader_module._systems_in_cents(
            [
                {
                    "system": "roof",
                    "repair_items": [
                        {
                            "item": "shingles",
                            "scope": "replace",
                            "cost_low_usd": 2500.75,
                            "cost_high_usd": 4000.0,
                            "confidence": 0.8,
                            "evidence_photo_ids": [0],
                        },
                    ],
                }
            ]
        )
        item = out[0]["repair_items"][0]
        assert item["cost_low_cents"] == 250075
        assert item["cost_high_cents"] == 400000
        assert "cost_low_usd" not in item
        assert "cost_high_usd" not in item
        assert item["item"] == "shingles"  # everything else survives

    def test_a_system_with_no_repair_items_is_unharmed(self) -> None:
        assert photo_grader_module._systems_in_cents([{"system": "roof"}]) == [
            {"system": "roof", "repair_items": []}
        ]
