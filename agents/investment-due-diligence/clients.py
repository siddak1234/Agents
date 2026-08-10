"""The agent's one outbound dependency: Claude, via the official SDK.

Every capability is the same shape — let Claude research the question with
the server-side web search tool, then require it to report through a named
tool whose schema mirrors the capability's `output_schema` in agent.yaml.
`research()` is that shape, and it is the only function here.

Why a forced tool rather than `output_config.format`: web search always
enables citations, and structured outputs reject a request that carries
them. A tool schema gets the same guarantee — the shape is part of what was
asked for rather than checked afterwards — without the conflict.

Failures come back as plain built-in exceptions that agent_main.py already
maps onto an envelope:
  - RuntimeError    -> config problem (missing key, a 4xx) -> unavailable, not retryable
  - ConnectionError -> transient problem (5xx, unusable answer) -> unavailable, retryable
  - TimeoutError    -> call didn't finish in time             -> timeout, retryable

This module also keeps the request's spend tally. `usage` reports the tokens
Claude charged and the model that produced them, never money — see
docs/AGENT_PROTOCOL.md.
"""

from __future__ import annotations

import os
from typing import Any

#: Anthropic's default for complex agentic work. Web search needs a model in
#: the 4.6-and-later family, which rules out the claude-sonnet-4-5 the other
#: agents in this repository pin.
MODEL = "claude-opus-5"

#: Dynamic filtering: Claude writes code that filters results before they
#: reach its context, which keeps a multi-search turn affordable. Requires a
#: 4.6-or-later model, which MODEL is.
WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "web_search_20260209",
    "name": "web_search",
    #: A hard ceiling on searches per request. The capabilities here are
    #: single-locality questions; without a cap an ambiguous address can turn
    #: one call into a research project.
    "max_uses": 6,
}

#: Room for search results, thinking and the final tool call.
MAX_TOKENS = 8192

#: The turn can pause when the server-side search loop hits its iteration
#: limit. Resuming is just re-sending the conversation; this bounds how many
#: times we will, so a pathological turn cannot loop forever.
MAX_RESUMES = 4

_spend: dict[str, object] = {"input_tokens": 0, "output_tokens": 0, "model": None}


def reset_spend() -> None:
    """Zeroes the tally. Called per request so in-process tests don't accrue."""
    _spend.update(input_tokens=0, output_tokens=0, model=None)


def spent_usage() -> dict[str, object]:
    """What this request has spent so far, in the `usage` wire shape."""
    return dict(_spend)


def _record(usage: Any) -> None:
    _spend["input_tokens"] = int(_spend["input_tokens"]) + int(usage.input_tokens)  # type: ignore[arg-type]
    _spend["output_tokens"] = int(_spend["output_tokens"]) + int(usage.output_tokens)  # type: ignore[arg-type]
    _spend["model"] = MODEL


def _client(timeout: float) -> Any:
    """Builds a client, or explains which credential is missing.

    Constructed per call rather than at import so `describe` never touches
    the SDK, and so a missing key is a disabled capability rather than an
    import-time crash (RULE 3).
    """
    import anthropic

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    return anthropic.Anthropic(timeout=timeout, max_retries=2)


def research(
    *,
    system: str,
    prompt: str,
    tool: dict[str, Any],
    timeout: float = 90.0,
) -> dict[str, Any]:
    """Research `prompt` with web search, and return `tool`'s arguments.

    Claude decides what to search; the tool schema decides what it must come
    back with. An answer that never calls the tool is a model failure rather
    than a caller error, so it surfaces as retryable `unavailable`.
    """
    import anthropic

    client = _client(timeout)
    tool_name = tool["name"]
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

    try:
        for _ in range(MAX_RESUMES + 1):
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system,
                tools=[WEB_SEARCH_TOOL, tool],
                messages=messages,
            )
            # Recorded before the response is inspected: those tokens are
            # billed whether or not the answer turns out to be usable.
            _record(response.usage)

            recorded = _tool_input(response, tool_name)
            if recorded is not None:
                return recorded

            if response.stop_reason != "pause_turn":
                break
            # A paused turn resumes by sending it back unchanged.
            messages.append({"role": "assistant", "content": response.content})
    except anthropic.APITimeoutError as exc:
        raise TimeoutError(f"Claude did not respond within {timeout:.0f}s") from exc
    except anthropic.APIConnectionError as exc:
        raise ConnectionError(f"Claude is unreachable: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise ConnectionError(f"Claude rate-limited this request: {exc}") from exc
    except anthropic.APIStatusError as exc:
        if exc.status_code >= 500:
            raise ConnectionError(f"Claude returned HTTP {exc.status_code}") from exc
        # 4xx is a configuration problem — a rejected key, a malformed
        # request. Retrying will not fix it.
        raise RuntimeError(f"Claude returned HTTP {exc.status_code}: {exc}") from exc

    raise ConnectionError(f"Claude never called {tool_name!r}; no usable answer was produced")


def _tool_input(response: Any, tool_name: str) -> dict[str, Any] | None:
    """The named tool's arguments, or None if this turn did not call it.

    Matched on name: the same turn carries `server_tool_use` blocks for web
    search, and reading those as the reporting tool's arguments would hand a
    caller a search query where it expected a result.
    """
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            recorded = block.input
            if not isinstance(recorded, dict):
                raise ConnectionError(f"Claude called {tool_name!r} with a non-object input")
            return recorded
    return None


def searches_performed(response: Any) -> int:
    """How many web searches a response billed. Used by tests, not by callers."""
    server = getattr(response.usage, "server_tool_use", None)
    return int(getattr(server, "web_search_requests", 0) or 0)
