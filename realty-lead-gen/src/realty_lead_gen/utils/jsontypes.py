"""The one alias we use for JSON-shaped data.

Every JSONB column, every LLM tool-use payload, and every webhook body in
this codebase is "a JSON object whose exact shape is not known to the type
checker". Spelling that ``dict[str, Any]`` at each site is fine but silent;
naming it says *why* the value is untyped, and gives us a single place to
tighten later.

Deliberately **not** a recursive union::

    JSONValue = str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]

That is more truthful and much worse to use: every read has to narrow the
union before it can index, so call sites fill up with ``isinstance`` ladders
or ``cast`` that assert the very thing the alias was supposed to prove. The
place to recover real types is at the boundary — a Pydantic model validating
the payload — not in the annotation on the column.
"""

from __future__ import annotations

from typing import Any

#: A decoded JSON object. Keys are strings; values are whatever the producer
#: put there. Validate into a Pydantic model before trusting the contents.
#:
#: PEP 695 form. This is not cosmetic: the `type` statement builds a *lazy*
#: `TypeAliasType`, so the three frameworks that read this alias at runtime
#: each have to unwrap it rather than see a plain `dict[str, Any]` —
#: SQLAlchemy resolving `Mapped[JSONDict]` into a JSONB column, Pydantic
#: building a validator for `criteria: JSONDict`, and FastAPI generating an
#: OpenAPI schema for `response_model=list[JSONDict]`. All three are
#: exercised by `tests/unit/test_jsontypes_alias.py`, which exists precisely
#: because "the type checker is happy" proves nothing about them.
type JSONDict = dict[str, Any]

__all__ = ["JSONDict"]
