"""Tavily search and Groq chat-completions clients.

API keys are read here via os.getenv at call time -- nothing else in this
agent touches os.environ for a credential. Both functions use plain
urllib (stdlib only, no dependencies). Per-call timeouts are supplied by
the caller from `DeadlineBudget` so sequential searches share one
`deadline_ms` instead of each taking a fixed 15s.

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
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Nominatim's usage policy requires a real identifying User-Agent on every
# request (generic/missing ones get blocked) and caps public usage at
# 1 request/second -- fine for this agent's one-lookup-per-call pattern.
# Groq (and some WAFs in front of it) also reject Python-urllib's default
# User-Agent with HTTP 403, so every outbound call uses this same identity.
USER_AGENT = "investment-due-diligence-agent/1.0"


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
            "api_key": api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": max_results,
            "include_answer": False,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        TAVILY_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    data = _call(request, timeout=timeout, service="Tavily search")
    results = data.get("results", [])
    return results if isinstance(results, list) else []


def groq_chat_json(
    prompt: str, *, system: str | None = None, timeout: float = 25
) -> tuple[dict[str, Any], dict[str, int]]:
    """Calls Groq's chat completions API, forcing a JSON object response.

    Returns (parsed_json, usage). cost_micros is derived from the model's
    published per-MTok rates — Groq's completions payload has tokens but
    not dollars.
    """
    api_key = _groq_api_key()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body = json.dumps(
        {
            "model": GROQ_MODEL,
            "messages": messages,
            "response_format": {"type": "json_object"},
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

    try:
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ConnectionError(f"Groq returned an unparsable response: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ConnectionError("Groq returned JSON that is not an object")

    raw_usage = data.get("usage") or {}
    input_tokens = int(raw_usage.get("prompt_tokens", 0) or 0)
    output_tokens = int(raw_usage.get("completion_tokens", 0) or 0)
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_micros": groq_cost_micros(input_tokens, output_tokens),
    }
    return parsed, usage


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
