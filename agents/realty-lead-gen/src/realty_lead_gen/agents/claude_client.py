"""Thin wrapper over the Anthropic SDK.

Centralizes:
    * client lifecycle (single async client per process)
    * token accounting — input/output counts and the model that produced them,
      reported in the envelope's `usage`
    * unified error mapping (transient vs permanent)
    * structured logging

No prices. `usage` reports what this agent observed, never money: a price is a
vendor fact that changes without notice, so cost is derived once wherever it is
needed rather than copied into every agent. See docs/AGENT_PROTOCOL.md — the
rate table this file used to hold was deleted with `cost_micros`, and this
paragraph outlived it by telling the next maintainer to bring it back.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import anthropic
from anthropic import AsyncAnthropic
from anthropic._exceptions import APIConnectionError, APIError, APIStatusError, RateLimitError
from anthropic.types import TextBlock, ToolUseBlock

from realty_lead_gen.config import Settings, get_settings
from realty_lead_gen.logging import get_logger
from realty_lead_gen.utils.http import is_transient_status
from realty_lead_gen.utils.retry import PermanentError, TransientError, default_retry

if TYPE_CHECKING:
    from realty_lead_gen.utils.jsontypes import JSONDict

logger = get_logger(__name__)


def text_of(message: anthropic.types.Message) -> str:
    """Concatenate every text block in a response, ignoring the rest.

    `Message.content` is a union that has grown to ten-plus block kinds
    (thinking, redacted thinking, tool use, server tool use, web search
    results, ...). Callers want the prose, and the two ways to get it differ
    in more than style: filtering on ``getattr(b, "type", None) == "text"``
    reads as equivalent but leaves the union unnarrowed, so ``b.text`` is
    unchecked — and it silently starts picking up any future block kind that
    also happens to carry ``type == "text"``. `isinstance` narrows for real.
    """
    return "".join(b.text for b in message.content if isinstance(b, TextBlock))


def tool_use_input(message: anthropic.types.Message, tool_name: str) -> JSONDict | None:
    """Return the arguments of the first `tool_name` invocation, or None.

    Filtering on the name (not merely on the block kind) matters even when
    the request pinned ``tool_choice`` to one tool: if the model ever emits a
    different tool, matching on kind alone hands its arguments to a caller
    that will read them as the expected schema and fail somewhere further
    down, with a stack trace that points at the wrong thing.
    """
    for block in message.content:
        if isinstance(block, ToolUseBlock) and block.name == tool_name:
            return block.input
    return None


@dataclass(frozen=True, slots=True)
class LlmUsage:
    """Token accounting for one LLM call, and the model that produced it.

    No money. A price is a vendor fact that changes without notice, and the
    copy kept here went stale unnoticed: it priced Opus threefold high and
    Haiku fourfold low, for a field nothing read. Cost is derived from
    `model` and the token counts wherever it is actually needed. See
    docs/AGENT_PROTOCOL.md.
    """

    model: str
    input_tokens: int
    output_tokens: int

    @classmethod
    def from_response(cls, model: str, usage: anthropic.types.Usage) -> LlmUsage:
        return cls(
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )


class ClaudeClient:
    """Process-local Anthropic client with retry + accounting."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        if self._settings.anthropic_api_key is None:
            self._client = None
        else:
            self._client = AsyncAnthropic(
                api_key=self._settings.anthropic_api_key.get_secret_value(),
                timeout=60.0,
            )

    @property
    def available(self) -> bool:
        return self._client is not None

    async def messages_create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        # Safe only on a model that still accepts a non-default sampling
        # parameter. claude-sonnet-4-5 (this agent's current default, see
        # config.py's `anthropic_model_vision`) does. claude-sonnet-5,
        # Opus 4.6 and later, and Fable 5 do not — they reject `temperature`
        # set to anything but the API default (1.0) with a 400 on every call.
        # commercial-lease-agent shipped exactly this bug: `temperature=0.0`
        # copied from here onto claude-sonnet-5, which made its only paid
        # capability non-functional until it was caught and removed. Before
        # pointing ANTHROPIC_MODEL_VISION at a newer model, remove this
        # parameter first, or confirm the target model still accepts it.
        temperature: float = 0.0,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        # ASYNC109 would have the caller wrap this in `asyncio.timeout`
        # instead of taking a `timeout` parameter. That is the right default
        # advice and the wrong call here: a caller-side timeout bounds the
        # *whole retry sequence*, while this one bounds each individual
        # attempt, which is what makes a hung single request recoverable
        # rather than fatal to the batch. The two are not interchangeable.
        timeout: float = 60.0,  # noqa: ASYNC109
    ) -> tuple[anthropic.types.Message, LlmUsage]:
        """Call Anthropic messages endpoint with retry + accounting."""
        if self._client is None:
            raise PermanentError("ANTHROPIC_API_KEY not configured")

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system is not None:
            kwargs["system"] = system
        if tools is not None:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice

        async for attempt in default_retry():
            with attempt:
                try:
                    resp = await asyncio.wait_for(
                        self._client.messages.create(**kwargs),
                        timeout=timeout,
                    )
                except (APIConnectionError, RateLimitError, TimeoutError) as e:
                    raise TransientError(str(e)) from e
                except APIStatusError as e:
                    # Same predicate the vendor HTTP adapters use, so "which
                    # statuses get another attempt" is one answer for the
                    # whole codebase rather than a per-client opinion.
                    if e.status_code and is_transient_status(e.status_code):
                        raise TransientError(str(e)) from e
                    raise PermanentError(str(e)) from e
                except APIError as e:
                    raise PermanentError(str(e)) from e
                usage = LlmUsage.from_response(model, resp.usage)
                logger.info(
                    "llm.call",
                    model=model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                )
                return resp, usage
        raise RuntimeError("unreachable")
