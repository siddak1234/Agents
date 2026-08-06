"""Tavily search and Groq chat-completions clients.

API keys are read here via os.getenv at call time -- nothing else in this
agent touches os.environ for a credential. Both functions use plain
urllib (stdlib only, no dependencies) and a fixed timeout per call
instead of a shared request-wide deadline: simple and good enough for
an agent that makes at most a couple of calls per capability.

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
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

TAVILY_URL = "https://api.tavily.com/search"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
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

    Returns (parsed_json, usage). cost_micros in usage is always 0 --
    Groq's completions API doesn't return pricing.
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

    raw_usage = data.get("usage") or {}
    usage = {
        "input_tokens": int(raw_usage.get("prompt_tokens", 0) or 0),
        "output_tokens": int(raw_usage.get("completion_tokens", 0) or 0),
        "cost_micros": 0,
    }
    return parsed, usage


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
    except socket.timeout as exc:
        raise TimeoutError(f"{service} did not respond within {timeout:.0f}s") from exc
    except urllib.error.URLError as exc:
        raise ConnectionError(f"{service} is unreachable: {exc.reason}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConnectionError(f"{service} returned invalid JSON") from exc