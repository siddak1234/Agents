"""Adapter registry.

Adapters register here so the orchestrator can enumerate + select them
without knowing the concrete classes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from realty_lead_gen.config import Settings, get_settings
from realty_lead_gen.sources.county_recorder import CountyRecorderAdapter
from realty_lead_gen.sources.fsbo import FsboAdapter
from realty_lead_gen.sources.propertyradar import PropertyRadarAdapter
from realty_lead_gen.sources.rapidapi_zillow import RapidApiZillowAdapter
from realty_lead_gen.sources.reso_mls import ResoMlsAdapter

if TYPE_CHECKING:
    from realty_lead_gen.sources.base import SourceAdapter

# Registration is source-order-significant: earlier entries are preferred
# when the same property surfaces from multiple sources (see pipeline.dedup).
_ADAPTER_ORDER: Final[tuple[str, ...]] = (
    ResoMlsAdapter.name,  # licensed feed — highest trust
    PropertyRadarAdapter.name,  # off-market signal source
    CountyRecorderAdapter.name,
    RapidApiZillowAdapter.name,  # last-resort portal fill-in
    FsboAdapter.name,
)


def _build_all(settings: Settings) -> dict[str, SourceAdapter]:
    """Instantiate every adapter (available or not)."""
    adapters: dict[str, SourceAdapter] = {
        ResoMlsAdapter.name: ResoMlsAdapter(settings),
        PropertyRadarAdapter.name: PropertyRadarAdapter(settings),
        CountyRecorderAdapter.name: CountyRecorderAdapter(),
        RapidApiZillowAdapter.name: RapidApiZillowAdapter(settings),
        FsboAdapter.name: FsboAdapter(),
    }
    return adapters


def get_adapter(name: str, settings: Settings | None = None) -> SourceAdapter:
    return _build_all(settings or get_settings())[name]


def all_adapters(settings: Settings | None = None) -> list[SourceAdapter]:
    """Every registered adapter in preference order, available or not.

    Distinct from `all_available_adapters` on purpose. The pipeline wants
    the filtered list — running an adapter with no credentials is wasted
    work. Diagnostics want the unfiltered one: "which sources exist and
    which are dark" is a different question from "which can I use now",
    and answering the first by reaching into `_build_all` is how a private
    helper quietly becomes public API without anyone deciding to make it so.
    """
    built = _build_all(settings or get_settings())
    return [built[name] for name in _ADAPTER_ORDER]


def all_available_adapters(settings: Settings | None = None) -> list[SourceAdapter]:
    """Return the set of adapters whose credentials are present, in preference order."""
    return [adapter for adapter in all_adapters(settings) if adapter.available]
