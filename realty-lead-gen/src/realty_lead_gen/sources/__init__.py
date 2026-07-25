"""Ingestion adapters.

Each adapter implements :class:`~realty_lead_gen.sources.base.SourceAdapter`
and emits normalized :class:`~realty_lead_gen.schemas.listing.RawListing`
records for downstream ingestion. Adapters are discovered by name via
:mod:`realty_lead_gen.sources.registry`.

Adapter design rules:
    * Never raise for missing credentials — return an empty iterator and
      mark the adapter :attr:`available` = False. The orchestrator
      handles adapter unavailability by falling back to other sources.
    * Rate-limit inside the adapter (adapter knows its own limits, the
      orchestrator does not).
    * Use ``utils.retry.default_retry`` for transient errors.
    * Never do partial writes. Yield a full ``RawListing`` per record.
"""

from realty_lead_gen.sources.base import SourceAdapter
from realty_lead_gen.sources.registry import all_available_adapters, get_adapter

__all__ = ["SourceAdapter", "all_available_adapters", "get_adapter"]
