"""Capability wiring, the client, and the envelope.

The agent's judgment now lives in Claude, so these tests do not assert what
a good answer looks like — that is not knowable offline. They assert the
parts that are still ours and still deterministic:

* input validation, which rejects a caller's mistake before anything is billed
* the tool each capability asks Claude to fill in
* the mapping from the tool's arguments onto the published output schema
* the client: usage recording, `pause_turn` resumption, and which failure
  becomes which envelope

Two stubbing depths, both offline. `analysis.research` /
`recommendation.research` are replaced where the assertion is about a
capability; a fake SDK client is injected where it is about `clients`
itself. No network, no key.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_main
import analysis
import clients
import recommendation
from budget import DeadlineBudget
from recommendation import RECOMMENDATION_TOOL, RECOMMENDATION_TOOL_NAME

FAKE_KEY = {"ANTHROPIC_API_KEY": "stub-not-a-real-key"}


def _capture(answer: dict):
    """Stands in for `research`, recording what it was asked."""
    seen: dict = {}

    def _fake(*, system: str, prompt: str, tool: dict, timeout: float = 90.0) -> dict:
        seen.update(system=system, prompt=prompt, tool=tool, timeout=timeout)
        return answer

    return seen, _fake


class TestPropertyIntelligence(unittest.TestCase):
    ANSWER: ClassVar[dict] = {
        "builder": "Prestige",
        "property_type": "Apartment",
        "configuration": "3BHK",
        "location": "Whitefield, Bangalore",
    }

    def test_maps_the_tool_answer_onto_the_published_shape(self):
        seen, fake = _capture(self.ANSWER)
        with mock.patch.object(analysis, "research", fake):
            out = analysis.build_property_profile(
                property_url=None,
                address="Prestige Lakeside Habitat, Whitefield, Bangalore",
                asking_price_inr=14_000_000,
                area_sqft=1650,
            )
        self.assertEqual(seen["tool"]["name"], "record_property_profile")
        self.assertEqual(out["builder"], "Prestige")
        self.assertEqual(out["location"], "Whitefield, Bangalore")
        # Derived locally: 14,000,000 / 1650. Arithmetic is not a judgment call.
        self.assertEqual(out["price_per_sqft"], 8484.85)
        # Stable for the same input, which a model cannot guarantee.
        self.assertTrue(out["property_id"].startswith("prop_"))

    def test_property_id_is_stable_across_calls(self):
        _, fake = _capture(self.ANSWER)
        with mock.patch.object(analysis, "research", fake):
            first = analysis.build_property_profile(
                property_url=None, address="12 MG Road, Pune", asking_price_inr=None, area_sqft=None
            )
            second = analysis.build_property_profile(
                property_url=None, address="12 MG Road, Pune", asking_price_inr=None, area_sqft=None
            )
        self.assertEqual(first["property_id"], second["property_id"])

    def test_a_residential_address_containing_shop_is_accepted(self):
        """"Bishop" contains "shop"; both are real Indian residential roads."""
        seen, fake = _capture(self.ANSWER)
        with mock.patch.object(analysis, "research", fake):
            analysis.build_property_profile(
                property_url=None,
                address="45 Bishop Cotton Road, Bangalore",
                asking_price_inr=9_000_000,
                area_sqft=1200,
            )
        self.assertIn("Bishop Cotton Road", seen["prompt"])

    def test_a_genuine_commercial_listing_is_refused_before_billing(self):
        called = False

        def _fake(**_kwargs):
            nonlocal called
            called = True
            return {}

        with mock.patch.object(analysis, "research", _fake), self.assertRaises(ValueError) as ctx:
            analysis.build_property_profile(
                property_url=None,
                address="Office Space, Commercial Tower, Gurgaon",
                asking_price_inr=None,
                area_sqft=None,
            )
        self.assertIn("residential", str(ctx.exception))
        self.assertFalse(called, "a caller's mistake must not reach a paid call")

    def test_a_negative_price_is_refused_before_billing(self):
        called = False

        def _fake(**_kwargs):
            nonlocal called
            called = True
            return {}

        with mock.patch.object(analysis, "research", _fake), self.assertRaises(ValueError):
            analysis.build_property_profile(
                property_url=None,
                address="12 MG Road, Pune",
                asking_price_inr=-100,
                area_sqft=1000,
            )
        self.assertFalse(called)


class TestOtherCapabilities(unittest.TestCase):
    def test_financial_analysis_passes_the_resolved_rate_and_maps_back(self):
        seen, fake = _capture({
            "market_value": "Overvalued",
            "premium_percent": 20.0,
            "estimated_rental_yield_percent": 3.5,
            "estimated_roi_percent": 7.5,
            "financial_score": 4.0,
        })
        with mock.patch.object(analysis, "research", fake):
            out = analysis.analyze_financials(
                location="Whitefield, Bangalore", asking_price_inr=14_000_000, area_sqft=1650
            )
        self.assertEqual(seen["tool"]["name"], "record_financial_analysis")
        self.assertIn("8484.85", seen["prompt"])
        self.assertEqual(out["market_value"], "Overvalued")
        self.assertEqual(out["financial_score"], 4.0)

    def test_financial_analysis_says_no_price_was_supplied(self):
        seen, fake = _capture({
            "market_value": None,
            "premium_percent": None,
            "estimated_rental_yield_percent": 3.0,
            "estimated_roi_percent": 11.0,
            "financial_score": 5.0,
        })
        with mock.patch.object(analysis, "research", fake):
            out = analysis.analyze_financials(location="Whitefield, Bangalore")
        self.assertIn("no asking price supplied", seen["prompt"])
        self.assertIsNone(out["market_value"])

    def test_location_requires_a_location(self):
        with self.assertRaises(ValueError):
            analysis.analyze_location(location="   ")

    def test_location_maps_the_answer(self):
        seen, fake = _capture({
            "connectivity_score": 7.0,
            "amenities_score": 8.0,
            "growth_potential": "High",
            "planned_infrastructure": ["Metro Phase 2"],
            "location_score": 8.5,
            "coordinates": {"latitude": 12.97, "longitude": 77.75},
        })
        with mock.patch.object(analysis, "research", fake):
            out = analysis.analyze_location(location="Whitefield, Bangalore")
        self.assertEqual(seen["tool"]["name"], "record_location_analysis")
        self.assertEqual(out["location_score"], 8.5)
        self.assertEqual(out["planned_infrastructure"], ["Metro Phase 2"])

    def test_risk_requires_a_location(self):
        with self.assertRaises(ValueError):
            analysis.assess_risk(location="")

    def test_risk_maps_the_answer_and_names_the_builder(self):
        seen, fake = _capture({
            "builder_legal_risk": "Medium",
            "environmental_risk": "Medium",
            "market_risk": "High",
            "overall_risk": "High",
            "identified_risks": ["one delayed project"],
        })
        with mock.patch.object(analysis, "research", fake):
            out = analysis.assess_risk(builder="Prestige", location="Whitefield, Bangalore")
        self.assertEqual(seen["tool"]["name"], "record_risk_assessment")
        self.assertIn("Prestige", seen["prompt"])
        self.assertEqual(out["overall_risk"], "High")

    def test_every_tool_bounds_its_scores_the_way_the_manifest_does(self):
        for tool in (analysis.FINANCIAL_TOOL, analysis.LOCATION_TOOL):
            for name, spec in tool["input_schema"]["properties"].items():
                if name.endswith("_score"):
                    self.assertEqual((spec["minimum"], spec["maximum"]), (0, 10), name)


class TestRecommendEvidence(unittest.TestCase):
    ANSWER: ClassVar[dict] = {
        "recommendation": "BUY",
        "confidence_percent": 72,
        "key_strengths": ["metro nearby"],
        "key_concerns": ["flood history"],
    }

    def test_nested_scores_are_lifted_out_of_raw_outputs(self):
        seen, fake = _capture(self.ANSWER)
        with mock.patch.object(recommendation, "research", fake):
            recommendation.recommend(
                financial_analysis={"financial_score": 7.5, "market_value": "Fair"},
                location_infrastructure_analysis={"location_score": 6.0},
                risk_assessment={"overall_risk": "Low"},
            )
        evidence = json.loads(seen["prompt"])["evidence"]
        self.assertEqual(evidence["financial_score"], 7.5)
        self.assertEqual(evidence["location_score"], 6.0)
        self.assertEqual(evidence["overall_risk"], "Low")

    def test_explicit_shortcut_beats_the_nested_value(self):
        seen, fake = _capture(self.ANSWER)
        with mock.patch.object(recommendation, "research", fake):
            recommendation.recommend(
                financial_analysis={"financial_score": 7.5}, financial_score=9.0
            )
        self.assertEqual(json.loads(seen["prompt"])["evidence"]["financial_score"], 9.0)

    def test_the_forced_tool_is_the_published_schema(self):
        seen, fake = _capture(self.ANSWER)
        with mock.patch.object(recommendation, "research", fake):
            out = recommendation.recommend(financial_score=7.5)
        self.assertIs(seen["tool"], RECOMMENDATION_TOOL)
        self.assertIn(RECOMMENDATION_TOOL_NAME, seen["system"])
        self.assertEqual(out["recommendation"], "BUY")

    def test_no_evidence_at_all_is_rejected_before_any_call(self):
        called = False

        def _fake(**_kwargs):
            nonlocal called
            called = True
            return {}

        with mock.patch.object(recommendation, "research", _fake), self.assertRaises(ValueError):
            recommendation.recommend()
        self.assertFalse(called)


# ---------------------------------------------------------------------------
# clients.research, against a fake SDK client. No network, no key.
# ---------------------------------------------------------------------------


class _Usage:
    def __init__(self, i: int = 100, o: int = 20) -> None:
        self.input_tokens = i
        self.output_tokens = o


class _ToolUse:
    type: ClassVar[str] = "tool_use"

    def __init__(self, name: str, payload) -> None:
        self.name = name
        self.input = payload


class _ServerToolUse:
    type: ClassVar[str] = "server_tool_use"
    name: ClassVar[str] = "web_search"
    input: ClassVar[dict] = {"query": "whatever"}


class _Response:
    def __init__(self, content, *, stop_reason="end_turn", usage=None) -> None:
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage or _Usage()


class _FakeMessages:
    def __init__(self, responses) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _FakeClient:
    def __init__(self, responses) -> None:
        self.messages = _FakeMessages(responses)


TOOL = {"name": "record_it", "description": "d", "input_schema": {"type": "object"}}


def _run(responses, **kwargs):
    fake = _FakeClient(responses)
    with (
        mock.patch.dict("os.environ", FAKE_KEY, clear=False),
        mock.patch.object(clients, "_client", lambda timeout: fake),
    ):
        return clients.research(system="s", prompt="p", tool=TOOL, **kwargs), fake


class TestResearchClient(unittest.TestCase):
    def setUp(self) -> None:
        clients.reset_spend()

    def test_returns_the_tool_arguments_and_offers_web_search(self):
        out, fake = _run([_Response([_ToolUse("record_it", {"answer": 1})])])
        self.assertEqual(out, {"answer": 1})
        tools = fake.messages.calls[0]["tools"]
        self.assertEqual(tools[0]["type"], "web_search_20260209")
        self.assertEqual(tools[1], TOOL)

    def test_usage_records_tokens_and_the_model_never_money(self):
        _run([_Response([_ToolUse("record_it", {"a": 1})], usage=_Usage(500, 60))])
        usage = clients.spent_usage()
        self.assertEqual(usage["input_tokens"], 500)
        self.assertEqual(usage["output_tokens"], 60)
        self.assertEqual(usage["model"], clients.MODEL)
        self.assertNotIn("cost_micros", usage)

    def test_a_search_block_is_not_mistaken_for_the_answer(self):
        """A server_tool_use block carries a query, not a result."""
        out, _ = _run([
            _Response([_ServerToolUse(), _ToolUse("record_it", {"answer": 2})]),
        ])
        self.assertEqual(out, {"answer": 2})

    def test_a_paused_turn_is_resumed(self):
        out, fake = _run([
            _Response([_ServerToolUse()], stop_reason="pause_turn"),
            _Response([_ToolUse("record_it", {"answer": 3})]),
        ])
        self.assertEqual(out, {"answer": 3})
        self.assertEqual(len(fake.messages.calls), 2)
        # Both turns are billed, so both must be counted.
        self.assertEqual(clients.spent_usage()["input_tokens"], 200)

    def test_never_calling_the_tool_is_retryable_unavailable(self):
        with self.assertRaises(ConnectionError) as ctx:
            _run([_Response([])])
        self.assertIn("record_it", str(ctx.exception))

    def test_tokens_are_recorded_even_when_no_answer_arrives(self):
        with self.assertRaises(ConnectionError):
            _run([_Response([], usage=_Usage(400, 10))])
        self.assertEqual(clients.spent_usage()["input_tokens"], 400)

    def test_a_missing_key_is_unavailable_not_a_crash(self):
        with mock.patch.dict("os.environ", {}, clear=True), self.assertRaises(RuntimeError) as ctx:
            clients.research(system="s", prompt="p", tool=TOOL)
        self.assertIn("ANTHROPIC_API_KEY is not configured", str(ctx.exception))


class TestEnvelopeWithoutCredentials(unittest.TestCase):
    """Every capability degrades to `unavailable` rather than crashing."""

    def setUp(self) -> None:
        clients.reset_spend()

    def _dispatch(self, capability: str, payload: dict) -> dict:
        request = json.dumps(
            {"protocol": "agentcall/v1", "capability": capability, "input": payload}
        )
        with mock.patch.dict("os.environ", {}, clear=True):
            return agent_main.dispatch(request)

    def test_each_capability_reports_unavailable(self):
        for capability, payload in (
            ("property_intelligence", {"address": "12 MG Road, Pune"}),
            ("financial_analysis", {"location": "Whitefield"}),
            ("location_infrastructure_analysis", {"location": "Whitefield"}),
            ("risk_assessment", {"location": "Whitefield"}),
            ("investment_recommendation", {"financial_score": 7.5}),
        ):
            with self.subTest(capability=capability):
                envelope = self._dispatch(capability, payload)
                self.assertFalse(envelope["ok"])
                self.assertEqual(envelope["error"]["type"], "unavailable")
                self.assertIn("ANTHROPIC_API_KEY", envelope["error"]["message"])
                self.assertEqual(envelope["usage"]["model"], None)

    def test_describe_answers_without_a_key(self):
        envelope = self._dispatch("describe", {})
        self.assertTrue(envelope["ok"])
        self.assertEqual(
            envelope["usage"], {"input_tokens": 0, "output_tokens": 0, "model": None}
        )


class TestTimeoutIsWorkable(unittest.TestCase):
    """The budget must leave room for a search-heavy model call.

    agent_main builds a DeadlineBudget for every capability except
    describe, so whatever `for_call` returns *is* the timeout `research`
    gets -- the 90s default never applies on a real call. When these
    constants were sized for single Tavily searches, that was 12s, which no
    web-search turn finishes inside: every real call would have timed out,
    and no test noticed because they all stub `research`.
    """

    #: Below this, a Claude turn that runs several searches cannot finish.
    FLOOR_S = 60.0

    def test_default_deadline_leaves_a_usable_timeout(self):
        budget = DeadlineBudget.from_deadline_ms(None)
        self.assertGreaterEqual(budget.for_call(1), self.FLOOR_S)

    def test_each_capability_receives_that_timeout(self):
        seen, fake = _capture({"property_type": "Apartment", "location": "Pune"})
        with mock.patch.object(analysis, "research", fake):
            analysis.build_property_profile(
                property_url=None,
                address="12 MG Road, Pune",
                asking_price_inr=None,
                area_sqft=None,
                budget=DeadlineBudget.from_deadline_ms(None),
            )
        self.assertGreaterEqual(seen["timeout"], self.FLOOR_S)


class TestToolSchemaGolden(unittest.TestCase):
    """The schema sent to the model is pinned, so changing it is deliberate."""

    GOLDEN = Path(__file__).resolve().parent / "golden" / "recommendation_tool_schema.json"

    def test_matches_the_golden_file(self):
        self.assertEqual(
            RECOMMENDATION_TOOL,
            json.loads(self.GOLDEN.read_text(encoding="utf-8")),
            "RECOMMENDATION_TOOL changed; review the diff and update the golden file.",
        )

    def test_is_in_the_anthropic_tool_shape(self):
        self.assertEqual(sorted(RECOMMENDATION_TOOL), ["description", "input_schema", "name"])
        required = RECOMMENDATION_TOOL["input_schema"]["required"]
        self.assertIn("recommendation", required)
        self.assertIn("confidence_percent", required)


if __name__ == "__main__":
    unittest.main()
