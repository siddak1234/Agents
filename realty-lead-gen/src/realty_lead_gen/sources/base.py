"""Common source adapter protocol + region descriptor.

We deliberately use a Protocol (structural typing) rather than an ABC
so adapters can be trivially mocked and no inheritance hierarchy exists
to maintain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from realty_lead_gen.schemas.listing import RawListing


@dataclass(frozen=True, slots=True)
class SearchRegion:
    """A geographic search scope. Any one of the three may be set;
    when more than one is set, all constraints apply.
    """

    postal_codes: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()
    # A vendor-specific region identifier (e.g. MLS name, PropertyRadar territory)
    regions: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not (self.postal_codes or self.cities or self.regions)


class SourceAdapter(Protocol):
    """Structural type every source adapter satisfies."""

    #: Stable identifier used by the registry and log correlation.
    name: str

    @property
    def available(self) -> bool:
        """True if the adapter has all it needs to run (credentials + config).

        Declared read-only rather than as a plain ``available: bool``
        attribute. A protocol *variable* is settable, and mypy rejects any
        implementation that narrows it to a ``@property`` — which is exactly
        how the credentialed adapters compute it (``reso_trestle_token is not
        None`` and friends). Nothing assigns to it, so read-only is both the
        accurate contract and the one plain class attributes still satisfy.
        """
        ...

    # Not `async def`, on purpose. Every implementation is an async *generator*
    # (`async def ... yield`), and calling one returns the iterator immediately
    # rather than a coroutine that has to be awaited first. Declaring the
    # protocol member `async def` would type it as
    # `Coroutine[..., AsyncIterator[RawListing]]`, which is a different thing:
    # mypy then rejects the orchestrator's `async for raw in adapter.fetch(...)`
    # with "has no attribute __aiter__ (not async iterable)". A plain `def`
    # returning `AsyncIterator` is the signature an async generator function
    # actually has, so implementations match structurally and call sites type.
    def fetch(
        self,
        region: SearchRegion,
        limit: int,
    ) -> AsyncIterator[RawListing]:
        """Yield normalized listings for the given region, up to ``limit``.

        Implementations must be single-flight per call and honor
        ``limit`` — the orchestrator uses it as a hard budget.
        """
        ...
