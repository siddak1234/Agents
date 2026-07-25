"""Conformance suite for the source adapter registry.

Every adapter satisfies the same `SourceAdapter` Protocol, but mypy only
checks that the *signatures* line up. Nothing until now checked the
behaviour the orchestrator actually depends on: that an adapter with no
credentials reports itself unavailable rather than raising, that `fetch`
hands back something you can `async for` over rather than a coroutine you
must await first, and that an unavailable adapter terminates immediately
instead of attempting I/O.

The suite is parameterized over the registry rather than over a hand-written
list of classes, so a sixth adapter that has not been taught these rules
fails here rather than in production.

It also pins the three-way taxonomy ARCHITECTURE.md §11 claims, which is the
part most likely to drift, because nothing else in the build can tell these
apart:

    deferred     `available` is a hardcoded class attribute and no credential
                 flips it on — `county_recorder`, `fsbo`.
    stub         `available` is credential-gated and does flip, but `fetch`
                 yields nothing *even when credentialed* — `reso_mls`,
                 `propertyradar`.
    implemented  credential-gated, and `fetch` has a real body that issues
                 requests — `rapidapi_zillow`.

A stub that quietly grows an implementation, or an implementation that
regresses to a stub, moves between those buckets, and this file is where
that shows up.
"""

from __future__ import annotations

import inspect
from typing import Final

import pytest

from realty_lead_gen.config import Settings
from realty_lead_gen.sources import registry
from realty_lead_gen.sources.base import SearchRegion

pytestmark = pytest.mark.unit

# The documented preference order (`registry._ADAPTER_ORDER`, ARCHITECTURE.md
# §11). Spelled out here on purpose rather than imported: a test that reads the
# constant it is checking asserts only that Python can copy a tuple. Reordering
# the registry changes dedup precedence — which source wins when the same
# property arrives twice — so it should require editing this line deliberately.
EXPECTED_ORDER: Final[tuple[str, ...]] = (
    "reso_mls",
    "propertyradar",
    "county_recorder",
    "rapidapi_zillow",
    "fsbo",
)

#: Adapter name -> the single `Settings` field that turns it on.
CREDENTIAL_GATE: Final[dict[str, str]] = {
    "reso_mls": "reso_trestle_token",
    "propertyradar": "propertyradar_api_token",
    "rapidapi_zillow": "rapidapi_key",
}

#: Adapters whose `available` is a hardcoded `False` class attribute.
DEFERRED: Final[tuple[str, ...]] = ("county_recorder", "fsbo")

#: Credential-gated adapters that still yield nothing once credentialed.
CREDENTIALED_STUBS: Final[tuple[str, ...]] = ("propertyradar", "reso_mls")

_REGION = SearchRegion(postal_codes=("94110",))


def _settings(**overrides: object) -> Settings:
    """Build `Settings` with the process environment held at arm's length.

    `_env_file=None` so a developer's local `.env` cannot change the outcome
    of the suite. conftest's `_clear_env` fixture already unsets every source
    credential in the environment, which matters because pydantic-settings
    reads environment variables whether or not an env file is in play.
    """
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def _all_credentials() -> Settings:
    return _settings(**dict.fromkeys(CREDENTIAL_GATE.values(), "not-a-real-token"))


def test_registry_exposes_the_documented_adapters_in_preference_order() -> None:
    assert [a.name for a in registry.all_adapters(_settings())] == list(EXPECTED_ORDER)


def test_registry_falls_back_to_process_settings() -> None:
    """`all_adapters()` with no argument is the shape the orchestrator calls.

    It routes through `get_settings()` instead of the caller's object, and
    that branch is otherwise never exercised — every other test here passes
    settings explicitly.
    """
    assert [a.name for a in registry.all_adapters()] == list(EXPECTED_ORDER)


def test_adapter_names_are_unique_and_match_their_registry_keys() -> None:
    """The name is a log-correlation key and a dedup key, so a collision is
    not a cosmetic problem: two sources would share one identity."""
    names = [a.name for a in registry.all_adapters(_settings())]
    assert len(set(names)) == len(names)
    for name in names:
        assert registry.get_adapter(name, _settings()).name == name


@pytest.mark.parametrize("name", EXPECTED_ORDER)
def test_no_adapter_is_available_without_credentials(name: str) -> None:
    """Nothing self-enables. An adapter that reported `True` here would be
    dispatched by the orchestrator and fail at request time instead."""
    assert registry.get_adapter(name, _settings()).available is False


def test_no_adapter_is_available_without_credentials_in_aggregate() -> None:
    assert registry.all_available_adapters(_settings()) == []


@pytest.mark.parametrize(("name", "field"), sorted(CREDENTIAL_GATE.items()))
def test_one_credential_enables_exactly_one_adapter(name: str, field: str) -> None:
    """The gates are independent, and each adapter reads its own setting.

    Asserting on the whole enabled list rather than on `adapter.available`
    is what makes this catch a miswired gate: an adapter that read a
    neighbour's token would show up as a second entry.
    """
    enabled = [a.name for a in registry.all_available_adapters(_settings(**{field: "tok"}))]
    assert enabled == [name]


def test_the_available_subset_preserves_preference_order() -> None:
    enabled = [a.name for a in registry.all_available_adapters(_all_credentials())]
    assert enabled == [name for name in EXPECTED_ORDER if name in CREDENTIAL_GATE]


@pytest.mark.parametrize("name", DEFERRED)
def test_deferred_adapters_cannot_be_switched_on_by_any_credential(name: str) -> None:
    """`available = False` is a class attribute on these two, not a property.

    Handing them every credential in the settings object is the strongest
    available statement that they are dark by construction rather than by
    configuration — which is what ARCHITECTURE.md §11 claims about them.
    """
    assert registry.get_adapter(name, _all_credentials()).available is False


@pytest.mark.parametrize("name", EXPECTED_ORDER)
async def test_fetch_yields_an_async_iterator_that_terminates_when_unavailable(
    name: str,
) -> None:
    """Two contracts in one, because they fail together.

    First, `fetch` returns an async *iterator*, not a coroutine. This is the
    entire reason `SourceAdapter.fetch` is declared `def` rather than
    `async def`: an async generator function returns its iterator on call,
    so the orchestrator's `async for raw in adapter.fetch(...)` binds
    directly. An adapter written as a plain `async def` returning a list
    would type-check against a sloppier protocol and then fail at runtime.

    Second, an unavailable adapter drains to empty rather than raising. The
    orchestrator runs adapters opportunistically; a missing credential is a
    normal state, not an error, and it must not abort the ingest run.
    """
    stream = registry.get_adapter(name, _settings()).fetch(_REGION, limit=10)
    assert not inspect.iscoroutine(stream)
    assert hasattr(stream, "__aiter__")
    assert [listing async for listing in stream] == []


@pytest.mark.parametrize("name", CREDENTIALED_STUBS)
async def test_credentialed_stubs_are_honest_about_yielding_nothing(name: str) -> None:
    """`available is True` and still no listings — the definition of a stub.

    This is deliberately an assertion rather than an omission. Both adapters
    log `*.not_implemented` and return before constructing any client, so the
    call is safe without a network stub, and the day someone implements one
    of them this test fails and forces the taxonomy in ARCHITECTURE.md §11 to
    be corrected in the same commit.
    """
    adapter = registry.get_adapter(name, _settings(**{CREDENTIAL_GATE[name]: "tok"}))
    assert adapter.available is True
    assert [listing async for listing in adapter.fetch(_REGION, limit=10)] == []


async def test_the_implemented_adapter_short_circuits_on_an_empty_region() -> None:
    """`rapidapi_zillow` is the one adapter with a real `fetch` body, so it is
    the one that must not be called credentialed with a live region here.

    An empty `SearchRegion` exits at the `no_query` guard before the first
    request is composed, which is what makes the assertion safe offline while
    still proving the credentialed path is reached — an unavailable adapter
    would have returned one branch earlier.
    """
    adapter = registry.get_adapter("rapidapi_zillow", _settings(rapidapi_key="tok"))
    assert adapter.available is True
    assert SearchRegion().is_empty()
    assert [listing async for listing in adapter.fetch(SearchRegion(), limit=10)] == []
