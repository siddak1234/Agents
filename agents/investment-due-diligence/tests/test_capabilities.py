"""Composition tests: the wiring between the helpers, and the envelope.

`test_scoring.py` covers the pure helpers one at a time. Everything those
helpers are *plugged into* -- which search feeds which score, which
arguments go where, what reaches `usage` -- used to be reachable only with
a Tavily or Groq key, so the whole layer went untested and mutations to it
survived a green suite.

Two stubbing depths are used here, deliberately:

* `analysis.tavily_search` is replaced directly where the assertion is
  about scoring composition.
* `urllib.request.urlopen` is replaced where the assertion is about the
  envelope, so the real `tavily_search`/`groq_tool_call`, the real error
  mapping and the real spend tally all run.

Both mean no network and no credentials. The fake keys below never reach a
socket because the transport itself is replaced -- a real key cannot leak
in either, since these set `os.environ` explicitly rather than reading it.
"""

from __future__ import annotations

import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_main
import analysis
import clients
import recommendation
from recommendation import RECOMMENDATION_TOOL, RECOMMENDATION_TOOL_NAME

FAKE_KEYS = {"TAVILY_API_KEY": "stub-not-a-real-key", "GROQ_API_KEY": "stub-not-a-real-key"}


def _hit(title: str = "", content: str = "") -> dict:
    return {"title": title, "content": content}


def _results(*hits: dict) -> dict:
    return {"results": list(hits)}


def _groq_payload(arguments: dict, *, name: str = RECOMMENDATION_TOOL_NAME) -> dict:
    """A Groq chat-completions response carrying one forced tool call."""
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"function": {"name": name, "arguments": json.dumps(arguments)}}
                    ]
                }
            }
        ],
        "usage": {"prompt_tokens": 259, "completion_tokens": 88},
    }


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _Transport:
    """Stands in for urlopen, routing on the URL and the posted query."""

    def __init__(self, routes: list[tuple[str, object]]) -> None:
        #: (fragment matched against url+body, payload | Exception to raise)
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, request: object, timeout: float | None = None) -> _FakeResponse:
        url = request.full_url
        body = request.data.decode("utf-8") if request.data else ""
        self.calls.append(url)
        for fragment, payload in self.routes:
            if fragment in url or fragment in body:
                if isinstance(payload, Exception):
                    raise payload
                return _FakeResponse(payload)
        raise AssertionError(f"no route for {url} / {body[:120]}")


def _dispatch(capability: str, payload: dict, transport: _Transport) -> dict:
    request = json.dumps(
        {"protocol": "agentcall/v1", "capability": capability, "input": payload}
    )
    with (
        mock.patch.dict("os.environ", FAKE_KEYS, clear=False),
        mock.patch("urllib.request.urlopen", transport),
    ):
        return agent_main.dispatch(request)


# ---------------------------------------------------------------------------
# Scoring composition -- which search feeds which score.
# ---------------------------------------------------------------------------


class TestFinancialComposition(unittest.TestCase):
    COMPARABLES = _results(
        _hit("Locality rates", "Average Rs. 8,000 / sqft in the area"),
        _hit("Nearby listings", "Going rate ₹10,000 per sqft near the lake"),
        _hit("Quarterly report", "INR 9,000/sq.ft reported last quarter"),
    )
    RENTALS = _results(_hit("Rental market", "Area averages 3.5% rental yield"))

    def _search(self, query: str, **_: object) -> list[dict]:
        if "price per sqft" in query:
            return self.COMPARABLES["results"]
        if "rental yield" in query:
            return self.RENTALS["results"]
        raise AssertionError(f"unexpected query {query!r}")

    def test_market_position_measured_against_comparables(self):
        with mock.patch.object(analysis, "tavily_search", self._search):
            out = analysis.analyze_financials(
                location="Whitefield, Bangalore", price_per_sqft=10_800
            )
        # median([8000, 10000, 9000]) = 9000; 10800 is 20% above it.
        self.assertEqual(out["premium_percent"], 20.0)
        self.assertEqual(out["market_value"], "Overvalued")
        self.assertEqual(out["estimated_rental_yield_percent"], 3.5)
        # 3.5 yield + 8.0 appreciation - 20% premium * 0.2 = 7.5
        self.assertEqual(out["estimated_roi_percent"], 7.5)
        # Pins the argument order of _score_financials(premium, yield, roi):
        # feeding those three in any other order lands on a different number.
        self.assertEqual(out["financial_score"], 2.0)

    def test_no_comparables_reports_unknown_not_fair(self):
        """Search returned results, none with an extractable price.

        This used to fall back to the property's own price, forcing
        premium_percent to 0.0 and market_value to "Fair" at *any* asking
        price -- a fabricated verdict indistinguishable from a real one.
        """

        def _search(query: str, **_: object) -> list[dict]:
            if "price per sqft" in query:
                return [_hit("Outlook", "Prices have risen steadily in this locality.")]
            return self.RENTALS["results"]

        with mock.patch.object(analysis, "tavily_search", _search):
            out = analysis.analyze_financials(
                location="Whitefield, Bangalore", price_per_sqft=10_800
            )
        self.assertIsNone(out["market_value"])
        self.assertIsNone(out["premium_percent"])
        # Yield and ROI still stand on the location alone.
        self.assertEqual(out["estimated_rental_yield_percent"], 3.5)
        self.assertEqual(out["estimated_roi_percent"], 11.5)
        self.assertEqual(out["financial_score"], 5.4)

    def test_cheaper_property_scores_higher_through_the_whole_function(self):
        with mock.patch.object(analysis, "tavily_search", self._search):
            cheap = analysis.analyze_financials(
                location="Whitefield, Bangalore", price_per_sqft=7_000
            )
            dear = analysis.analyze_financials(
                location="Whitefield, Bangalore", price_per_sqft=13_000
            )
        self.assertEqual(cheap["market_value"], "Undervalued")
        self.assertEqual(dear["market_value"], "Overvalued")
        self.assertGreater(cheap["financial_score"], dear["financial_score"])


class TestLocationComposition(unittest.TestCase):
    def _search(self, query: str, **_: object) -> list[dict]:
        if "infrastructure" in query:
            return [_hit("Transit", "New Metro Line Phase 2 announced")]
        if "connectivity" in query:
            return [_hit("Neighbourhood", "Metro station, schools and hospitals nearby")]
        raise AssertionError(f"unexpected query {query!r}")

    def test_location_score_averages_connectivity_and_amenities(self):
        with (
            mock.patch.object(analysis, "tavily_search", self._search),
            mock.patch.object(analysis, "geocode", lambda *a, **k: None),
        ):
            out = analysis.analyze_location(location="Whitefield, Bangalore")
        self.assertEqual(out["connectivity_score"], 6.0)  # metro
        self.assertEqual(out["amenities_score"], 7.0)  # school, hospital
        self.assertEqual(out["growth_potential"], "High")  # metro keyword
        # (6.0 + 7.0) / 2 + 1.0 High-growth bonus. Averaging is load-bearing:
        # summing or multiplying instead lands somewhere else.
        self.assertEqual(out["location_score"], 7.5)

    def test_geocode_miss_leaves_coordinates_null_without_failing(self):
        with (
            mock.patch.object(analysis, "tavily_search", self._search),
            mock.patch.object(analysis, "geocode", lambda *a, **k: None),
        ):
            out = analysis.analyze_location(location="Nowhere, Nowhere")
        self.assertIsNone(out["coordinates"])
        self.assertEqual(out["location_score"], 7.5)


class TestRiskComposition(unittest.TestCase):
    def _search(self, query: str, **_: object) -> list[dict]:
        if "complaints litigation" in query:
            return [_hit("Coverage", "Project delay and litigation reported")]
        if "builder track record" in query:
            # Run even with no builder to name, so a missing key still
            # fails `unavailable` instead of reporting a false "Low".
            return []
        if "flooding" in query:
            return [_hit("Civic", "Occasional flooding reported near the lake")]
        if "slowdown" in query:
            return [_hit("Market", "Market slowdown and oversupply of inventory")]
        raise AssertionError(f"unexpected query {query!r}")

    def test_overall_risk_reflects_all_three_signals(self):
        with mock.patch.object(analysis, "tavily_search", self._search):
            out = analysis.assess_risk(
                builder="Prestige", location="Whitefield, Bangalore", property_type="Apartment"
            )
        self.assertEqual(out["builder_legal_risk"], "Medium")  # delay + litigation
        self.assertEqual(out["environmental_risk"], "Medium")  # one flood signal
        self.assertEqual(out["market_risk"], "High")  # slowdown + oversupply
        # Dropping any one of the three from the combine changes this.
        self.assertEqual(out["overall_risk"], "High")

    def test_missing_builder_is_a_stated_limitation_not_a_pass(self):
        with mock.patch.object(analysis, "tavily_search", self._search):
            out = analysis.assess_risk(location="Whitefield, Bangalore")
        self.assertEqual(out["builder_legal_risk"], "Medium")
        self.assertTrue(
            any("builder could not be identified" in r for r in out["identified_risks"])
        )


# ---------------------------------------------------------------------------
# Evidence merge -- what actually reaches the model.
# ---------------------------------------------------------------------------


class TestRecommendEvidence(unittest.TestCase):
    def _capture(self, answer: dict | None = None):
        """Replaces groq_tool_call, recording what it was asked."""
        sent = {}

        def _fake(prompt: str, *, tool: dict, system: str, timeout: float) -> dict:
            sent["evidence"] = json.loads(prompt)["evidence"]
            sent["tool"] = tool
            sent["system"] = system
            sent["timeout"] = timeout
            return answer or {
                "recommendation": "BUY",
                "confidence_percent": 72,
                "key_strengths": ["metro nearby"],
                "key_concerns": ["flood history"],
            }

        return sent, _fake

    def test_nested_scores_are_lifted_out_of_raw_outputs(self):
        sent, fake = self._capture()
        with mock.patch.object(recommendation, "groq_tool_call", fake):
            recommendation.recommend(
                financial_analysis={"financial_score": 7.5, "market_value": "Fair"},
                location_infrastructure_analysis={"location_score": 6.0},
                risk_assessment={"overall_risk": "Low"},
            )
        # Looking these up under the wrong key would silently drop them.
        self.assertEqual(sent["evidence"]["financial_score"], 7.5)
        self.assertEqual(sent["evidence"]["location_score"], 6.0)
        self.assertEqual(sent["evidence"]["overall_risk"], "Low")
        self.assertEqual(sent["evidence"]["financial_analysis"]["market_value"], "Fair")

    def test_explicit_shortcut_beats_the_nested_value(self):
        sent, fake = self._capture()
        with mock.patch.object(recommendation, "groq_tool_call", fake):
            recommendation.recommend(
                financial_analysis={"financial_score": 7.5}, financial_score=9.0
            )
        self.assertEqual(sent["evidence"]["financial_score"], 9.0)

    def test_the_forced_tool_is_the_published_schema(self):
        sent, fake = self._capture()
        with mock.patch.object(recommendation, "groq_tool_call", fake):
            out = recommendation.recommend(financial_score=7.5)
        self.assertIs(sent["tool"], RECOMMENDATION_TOOL)
        self.assertIn(RECOMMENDATION_TOOL_NAME, sent["system"])
        self.assertEqual(out["recommendation"], "BUY")
        self.assertEqual(out["confidence_percent"], 72)

    def test_no_evidence_at_all_is_rejected_before_any_call(self):
        called = False

        def _fake(*a: object, **k: object) -> dict:
            nonlocal called
            called = True
            return {}

        with (
            mock.patch.object(recommendation, "groq_tool_call", _fake),
            self.assertRaises(ValueError),
        ):
            recommendation.recommend()
        self.assertFalse(called)


# ---------------------------------------------------------------------------
# Envelope and accounting, over the real client code with a fake transport.
# ---------------------------------------------------------------------------


class TestEnvelopeAndSpend(unittest.TestCase):
    def setUp(self) -> None:
        clients.reset_spend()

    def test_financial_analysis_reports_two_searches_of_spend(self):
        transport = _Transport(
            [
                (
                    "price per sqft",
                    _results(_hit("Rates", "Average Rs. 9,000 / sqft in the area")),
                ),
                ("rental yield", _results(_hit("Rent", "Area averages 3.5% rental yield"))),
            ]
        )
        envelope = _dispatch(
            "financial_analysis",
            {"location": "Whitefield, Bangalore", "price_per_sqft": 10_800},
            transport,
        )
        self.assertTrue(envelope["ok"], envelope)
        self.assertEqual(envelope["output"]["market_value"], "Overvalued")
        self.assertEqual(len(transport.calls), 2)
        # Search spends Tavily credits, not tokens, and calls no model: the
        # envelope reports zeros and a null model rather than inventing a
        # number. Search count is asserted above, where it is observable.
        self.assertEqual(envelope["usage"], {
            "input_tokens": 0,
            "output_tokens": 0,
            "model": None,
        })

    def test_location_bills_searches_but_not_geocoding(self):
        transport = _Transport(
            [
                ("nominatim", [{"lat": "12.9698", "lon": "77.7500"}]),
                ("infrastructure", _results(_hit("Transit", "New Metro Line announced"))),
                (
                    "connectivity",
                    _results(_hit("Area", "Metro station, schools and hospitals nearby")),
                ),
            ]
        )
        envelope = _dispatch(
            "location_infrastructure_analysis", {"location": "Whitefield, Bangalore"}, transport
        )
        self.assertTrue(envelope["ok"], envelope)
        self.assertEqual(envelope["output"]["coordinates"], {"latitude": 12.9698, "longitude": 77.75})
        self.assertEqual(len(transport.calls), 3)  # geocode + 2 searches
        self.assertIsNone(envelope["usage"]["model"])

    def test_risk_assessment_bills_three_searches(self):
        transport = _Transport(
            [
                ("complaints litigation", _results(_hit("News", "delay reported"))),
                ("flooding", _results(_hit("Civic", "flooding near the lake"))),
                ("slowdown", _results(_hit("Market", "slowdown and oversupply"))),
            ]
        )
        envelope = _dispatch(
            "risk_assessment",
            {"builder": "Prestige", "location": "Whitefield, Bangalore"},
            transport,
        )
        self.assertTrue(envelope["ok"], envelope)
        self.assertEqual(len(transport.calls), 3)
        self.assertIsNone(envelope["usage"]["model"])

    def test_a_residential_address_containing_shop_is_accepted(self):
        """"Bishop" contains "shop"; both are real Indian residential roads."""
        transport = _Transport([("tavily", _results(_hit("Listing", "3 BHK for sale")))])
        envelope = _dispatch(
            "property_intelligence",
            {
                "address": "45 Bishop Cotton Road, Bangalore",
                "asking_price_inr": 9_000_000,
                "area_sqft": 1_200,
            },
            transport,
        )
        self.assertTrue(envelope["ok"], envelope)
        self.assertEqual(envelope["output"]["location"], "45 Bishop Cotton Road, Bangalore")
        self.assertEqual(envelope["output"]["price_per_sqft"], 7_500.0)

    def test_a_genuine_commercial_listing_is_still_refused(self):
        transport = _Transport([("tavily", _results())])
        envelope = _dispatch(
            "property_intelligence",
            {"address": "Office Space, Commercial Tower, Gurgaon"},
            transport,
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")
        self.assertIn("residential", envelope["error"]["message"])

    def test_spend_before_a_failure_is_still_reported(self):
        """One search succeeded, the next 500'd. The first one was paid for."""
        transport = _Transport(
            [
                (
                    "price per sqft",
                    _results(_hit("Rates", "Average Rs. 9,000 / sqft in the area")),
                ),
                (
                    "rental yield",
                    urllib.error.HTTPError(
                        "https://api.tavily.com/search", 500, "Server Error", None, None
                    ),
                ),
            ]
        )
        envelope = _dispatch(
            "financial_analysis", {"location": "Whitefield, Bangalore"}, transport
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "unavailable")
        self.assertTrue(envelope["error"]["retryable"])
        self.assertIsNone(envelope["usage"]["model"])

    def test_recommendation_forces_the_tool_and_reports_tokens(self):
        transport = _Transport(
            [
                (
                    "groq",
                    _groq_payload(
                        {
                            "recommendation": "NEGOTIATE",
                            "confidence_percent": 64,
                            "key_strengths": ["good yield"],
                            "key_concerns": ["no location evidence"],
                            "recommended_offer_price_inr": 8_500_000,
                        }
                    ),
                )
            ]
        )
        envelope = _dispatch(
            "investment_recommendation",
            {"financial_score": 7.5, "asking_price_inr": 9_000_000},
            transport,
        )
        self.assertTrue(envelope["ok"], envelope)
        self.assertEqual(envelope["output"]["recommendation"], "NEGOTIATE")
        self.assertEqual(envelope["output"]["recommended_offer_price_inr"], 8_500_000)
        self.assertEqual(envelope["usage"], {
            "input_tokens": 259,
            "output_tokens": 88,
            "model": "llama-3.3-70b-versatile",
        })

    def test_tavily_request_authenticates_by_header_not_body(self):
        """Tavily documents Bearer auth; the key must never be in the payload."""
        seen = {}

        class _Recording(_Transport):
            def __call__(self, request, timeout=None):
                seen["auth"] = request.headers.get("Authorization")
                seen["body"] = json.loads(request.data.decode("utf-8"))
                return super().__call__(request, timeout)

        _dispatch(
            "location_infrastructure_analysis",
            {"location": "Whitefield, Bangalore"},
            _Recording([
                ("nominatim", []),
                ("infrastructure", _results(_hit("T", "Metro announced"))),
                ("connectivity", _results(_hit("A", "schools nearby"))),
            ]),
        )
        self.assertEqual(seen["auth"], f"Bearer {FAKE_KEYS['TAVILY_API_KEY']}")
        self.assertNotIn("api_key", seen["body"])
        self.assertIn("query", seen["body"])

    def test_groq_request_carries_the_forced_tool_choice(self):
        sent = {}
        payload = _groq_payload(
            {
                "recommendation": "BUY",
                "confidence_percent": 80,
                "key_strengths": [],
                "key_concerns": [],
            }
        )

        class _Recording(_Transport):
            def __call__(self, request, timeout=None):
                sent.update(json.loads(request.data.decode("utf-8")))
                return super().__call__(request, timeout)

        _dispatch(
            "investment_recommendation",
            {"financial_score": 7.5},
            _Recording([("groq", payload)]),
        )
        self.assertEqual(
            sent["tool_choice"],
            {"type": "function", "function": {"name": RECOMMENDATION_TOOL_NAME}},
        )
        self.assertEqual(sent["tools"], [RECOMMENDATION_TOOL])
        self.assertNotIn("response_format", sent)

    def test_tokens_are_reported_even_when_the_answer_is_rejected(self):
        """The model called the tool but omitted `recommendation`.

        The call is billed whether or not its arguments are usable, so the
        envelope has to carry those tokens rather than zeroing them.
        """
        transport = _Transport([("groq", _groq_payload({"confidence_percent": 70}))])
        envelope = _dispatch(
            "investment_recommendation", {"financial_score": 7.5}, transport
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "unavailable")
        self.assertTrue(envelope["error"]["retryable"])
        self.assertEqual(envelope["usage"]["input_tokens"], 259)
        self.assertEqual(envelope["usage"]["model"], "llama-3.3-70b-versatile")

    def test_a_different_tool_name_is_refused(self):
        transport = _Transport(
            [(
                "groq",
                _groq_payload(
                    {"recommendation": "BUY", "confidence_percent": 80},
                    name="something_else",
                ),
            )]
        )
        envelope = _dispatch(
            "investment_recommendation", {"financial_score": 7.5}, transport
        )
        self.assertFalse(envelope["ok"])
        self.assertIn("something_else", envelope["error"]["message"])

    def test_describe_spends_nothing_and_ignores_a_bad_deadline(self):
        request = json.dumps(
            {"protocol": "agentcall/v1", "capability": "describe", "deadline_ms": "soon"}
        )
        envelope = agent_main.dispatch(request)
        self.assertTrue(envelope["ok"], envelope)
        self.assertEqual(
            envelope["usage"], {"input_tokens": 0, "output_tokens": 0, "model": None}
        )

    def test_a_bad_deadline_still_fails_a_capability_that_uses_it(self):
        request = json.dumps(
            {
                "protocol": "agentcall/v1",
                "capability": "financial_analysis",
                "input": {"location": "Whitefield"},
                "deadline_ms": "soon",
            }
        )
        envelope = agent_main.dispatch(request)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")


class TestToolSchemaGolden(unittest.TestCase):
    """The schema sent to the model is pinned, so changing it is deliberate."""

    GOLDEN = Path(__file__).resolve().parent / "golden" / "recommendation_tool_schema.json"

    def test_matches_the_golden_file(self):
        self.assertEqual(
            RECOMMENDATION_TOOL,
            json.loads(self.GOLDEN.read_text(encoding="utf-8")),
            "RECOMMENDATION_TOOL changed; review the diff and update the golden file.",
        )

    def test_required_fields_match_what_the_envelope_promises(self):
        required = RECOMMENDATION_TOOL["function"]["parameters"]["required"]
        # agent.yaml's output_schema requires these two of the caller.
        self.assertIn("recommendation", required)
        self.assertIn("confidence_percent", required)


if __name__ == "__main__":
    unittest.main()
