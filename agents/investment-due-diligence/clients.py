"""Tavily search, Groq tool calls, and OpenStreetMap geocoding.

API keys are read here via os.getenv at call time -- nothing else in this
agent touches os.environ for a credential. Every call uses plain urllib
(stdlib only, no dependencies). Per-call timeouts are supplied by the
caller from `DeadlineBudget` so sequential searches share one
`deadline_ms` instead of each taking a fixed 15s.

This module also keeps the request's spend tally: it is the only file that
spends anything, so cost is recorded here as it is incurred and read once
by agent_main.py when it builds the envelope. See `_spend` below.

Callers never see urllib or HTTP status codes -- failures come back as
plain built-in exceptions that agent_main.py already knows how to turn
into an envelope:
  - RuntimeError    -> config problem (missing key, a 4xx) -> unavailable, not retryable
  - ConnectionError -> transient problem (5xx, unreachable) -> unavailable, retryable
  - TimeoutError    -> call didn't finish in time           -> timeout, retryable
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

TAVILY_URL = "https://api.tavily.com/search"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
# Cap output: recommendation is a five-field JSON object; a few hundred
# tokens is enough. Without a ceiling a degenerate completion can run away.
GROQ_MAX_TOKENS = 512
# Groq on-demand list price for llama-3.3-70b-versatile (USD per 1M tokens).
# Reference: console.groq.com/docs/model/llama-3.3-70b-versatile
GROQ_INPUT_USD_PER_MTOK = 0.59
GROQ_OUTPUT_USD_PER_MTOK = 0.79
# Tavily bills per API credit. Every search this agent runs is
# `search_depth: "basic"`, which costs 1 credit; advanced would cost 2.
# Reference: docs.tavily.com/documentation/api-credits and tavily.com/pricing
TAVILY_CREDITS_PER_SEARCH = 1
TAVILY_USD_PER_CREDIT = 0.008
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Nominatim's usage policy requires a real identifying User-Agent on every
# request (generic/missing ones get blocked) and caps public usage at
# 1 request/second -- fine for this agent's one-lookup-per-call pattern.
# Groq (and some WAFs in front of it) also reject Python-urllib's default
# User-Agent with HTTP 403, so every outbound call uses this same identity.
USER_AGENT = "investment-due-diligence-agent/1.0"


# Everything this process has spent. The agent is called, not booted -- one
# request per process -- so a module-level tally is the whole request's
# spend. It lives here because this is the only file that spends anything,
# and it is recorded at the moment of spending rather than returned up the
# call stack, so a request that paid for two searches and *then* failed
# still reports what it spent instead of zero. agent_main.py reads it once,
# where the envelope is built.
_spend = {"input_tokens": 0, "output_tokens": 0, "cost_micros": 0}


def reset_spend() -> None:
    """Zeroes the tally. Called per request so in-process tests don't accrue."""
    _spend.update(input_tokens=0, output_tokens=0, cost_micros=0)


def spent_usage() -> dict[str, int]:
    """What this request has spent so far, in the `usage` wire shape."""
    return dict(_spend)


def _record(*, input_tokens: int = 0, output_tokens: int = 0, cost_micros: int = 0) -> None:
    _spend["input_tokens"] += input_tokens
    _spend["output_tokens"] += output_tokens
    _spend["cost_micros"] += cost_micros


def tavily_cost_micros(searches: int) -> int:
    """USD micros (1_000_000 = $1) for `searches` basic Tavily searches."""
    return round(searches * TAVILY_CREDITS_PER_SEARCH * TAVILY_USD_PER_CREDIT * 1_000_000)


def _tavily_api_key() -> str | None:
    return os.getenv("TAVILY_API_KEY")


def _groq_api_key() -> str | None:
    return os.getenv("GROQ_API_KEY")


def geocode(location: str, *, timeout: float = 10) -> dict[str, float] | None:
    """Resolves a place name to coordinates via OpenStreetMap's Nominatim.

    No API key required. Returns None (never raises) when the place can't
    be resolved -- geocoding here is corroboration, not a hard dependency,
    so a miss shouldn't fail whichever capability calls this.
    """
    params = urllib.parse.urlencode({"q": location, "format": "json", "limit": 1})
    request = urllib.request.Request(
        f"{NOMINATIM_URL}?{params}",
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        results = json.loads(raw)
    except Exception:
        return None

    if not results:
        return None
    try:
        return {"latitude": float(results[0]["lat"]), "longitude": float(results[0]["lon"])}
    except (KeyError, ValueError, TypeError):
        return None


def tavily_search(query: str, *, max_results: int = 5, timeout: float = 15) -> list[dict[str, Any]]:
    """Runs a Tavily search and returns its `results` list."""
    api_key = _tavily_api_key()
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY is not configured")

    body = json.dumps(
        {
            "query": query,
            "search_depth": "basic",
            "max_results": max_results,
            "include_answer": False,
        }
    ).encode("utf-8")
    # Bearer header, not an `api_key` field in the body. Tavily's current API
    # reference documents header auth only, and the official client
    # (tavily-ai/tavily-python) sends `Authorization: Bearer <key>` and never
    # puts the key in the payload. Body auth is a legacy form: undocumented
    # today, and if it stops being honoured every search here returns 401 ->
    # `unavailable`, which reads like graceful degradation while meaning the
    # agent never works at all. Keeping the key out of the body also keeps it
    # out of anything that logs request payloads.
    request = urllib.request.Request(
        TAVILY_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": USER_AGENT,
        },
    )
    data = _call(request, timeout=timeout, service="Tavily search")
    # Counted only once the call came back 200: a rejected key, a 429 or a
    # connection failure raises out of `_call` above and bills nothing.
    _record(cost_micros=tavily_cost_micros(1))
    results = data.get("results", [])
    return results if isinstance(results, list) else []


def groq_tool_call(
    prompt: str, *, tool: dict[str, Any], system: str | None = None, timeout: float = 25
) -> dict[str, Any]:
    """Calls Groq, forcing one named tool call, and returns its arguments.

    `response_format: json_object` only guaranteed *syntax* — the model
    could return `{}` and the caller had to invent the missing fields.
    Forcing a named tool puts the caller's JSON Schema in the request, so
    the shape is part of what was asked for rather than something checked
    after the fact. Groq answers a tool the model refuses to call with
    HTTP 400, which surfaces here as `unavailable` carrying Groq's detail.

    Returns the tool's arguments. Token spend is recorded to this module's
    tally rather than returned, so it is reported even when the arguments
    turn out to be unusable; cost is derived from the model's published
    per-MTok rates, since Groq's payload has tokens but not dollars.
    """
    api_key = _groq_api_key()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured")

    tool_name = tool["function"]["name"]
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body = json.dumps(
        {
            "model": GROQ_MODEL,
            "messages": messages,
            "tools": [tool],
            "tool_choice": {"type": "function", "function": {"name": tool_name}},
            "temperature": 0.2,
            "max_tokens": GROQ_MAX_TOKENS,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        GROQ_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": USER_AGENT,
        },
    )
    data = _call(request, timeout=timeout, service="Groq")

    # Recorded before the response is inspected: those tokens are billed
    # whether or not the model came back with a tool call anyone can use.
    raw_usage = data.get("usage") or {}
    input_tokens = int(raw_usage.get("prompt_tokens", 0) or 0)
    output_tokens = int(raw_usage.get("completion_tokens", 0) or 0)
    _record(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_micros=groq_cost_micros(input_tokens, output_tokens),
    )

    try:
        call = data["choices"][0]["message"]["tool_calls"][0]
        if call["function"]["name"] != tool_name:
            raise ConnectionError(
                f"Groq called {call['function']['name']!r}, not the required {tool_name!r}"
            )
        parsed = json.loads(call["function"]["arguments"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ConnectionError(f"Groq returned an unparsable tool call: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ConnectionError("Groq returned tool arguments that are not an object")

    return parsed


def groq_cost_micros(input_tokens: int, output_tokens: int) -> int:
    """USD micros (1_000_000 = $1) for a Groq llama-3.3-70b-versatile call."""
    cost_dollars = (input_tokens / 1_000_000) * GROQ_INPUT_USD_PER_MTOK + (
        output_tokens / 1_000_000
    ) * GROQ_OUTPUT_USD_PER_MTOK
    return round(cost_dollars * 1_000_000)


def _call(request: urllib.request.Request, *, timeout: float, service: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            detail = ""
        suffix = f": {detail}" if detail else ""
        if exc.code >= 500 or exc.code == 429:
            raise ConnectionError(f"{service} returned HTTP {exc.code}{suffix}") from exc
        raise RuntimeError(f"{service} returned HTTP {exc.code}{suffix}") from exc
    except TimeoutError as exc:
        raise TimeoutError(f"{service} did not respond within {timeout:.0f}s") from exc
    except urllib.error.URLError as exc:
        raise ConnectionError(f"{service} is unreachable: {exc.reason}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConnectionError(f"{service} returned invalid JSON") from exc
