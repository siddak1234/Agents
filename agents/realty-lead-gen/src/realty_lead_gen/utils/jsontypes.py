"""The one alias we use for JSON-shaped data.

An LLM tool-use payload is "a JSON object whose exact shape is not known to
the type checker". Spelling that ``dict[str, Any]`` at each site is fine but
silent; naming it says *why* the value is untyped, and gives us a single
place to tighten later.

Deliberately **not** a recursive union::

    JSONValue = str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]

That is more truthful and much worse to use: every read has to narrow the
union before it can index, so call sites fill up with ``isinstance`` ladders
or ``cast`` that assert the very thing the alias was supposed to prove. The
place to recover real types is at the boundary, where the payload is read
into a typed structure — not in the annotation that carries it there.
"""

from __future__ import annotations

from typing import Any

#: A decoded JSON object. Keys are strings; values are whatever the producer
#: put there — here, the Anthropic tool-use payload that
#: `agents.claude_client.tool_use_input` returns. Check the contents before
#: trusting them: nothing between the model and this dict validates them.
type JSONDict = dict[str, Any]

__all__ = ["JSONDict"]
