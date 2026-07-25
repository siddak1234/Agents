"""The `JSONDict` alias, as the three frameworks that read it at runtime see it.

`type JSONDict = dict[str, Any]` (PEP 695) is not the same object as
`JSONDict: TypeAlias = dict[str, Any]`. The `type` statement builds a lazy
`TypeAliasType` whose `__value__` is only evaluated on demand, so anything
that introspects the annotation has to unwrap it. Three things in this
codebase do exactly that, and none of them is checked by mypy:

* **SQLAlchemy** resolves `Mapped[JSONDict]` to decide the column type.
  A failure here is silent-ish at import time and catastrophic at migration
  time.
* **Pydantic** builds a validator for `criteria: JSONDict`.
* **FastAPI** generates OpenAPI from `response_model=list[JSONDict]` — and
  that document is the contract the frontend generates its client from, so
  a change in its shape is a wire-visible change even when the Python
  behaviour is identical.

These assertions are the evidence for the alias form, not decoration. If a
future SQLAlchemy or Pydantic release regresses on lazy aliases, this fails
here rather than in a migration.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any, get_args, get_origin

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import configure_mappers

from realty_lead_gen.config import get_settings
from realty_lead_gen.main import create_app
from realty_lead_gen.models.deal import DealAnalysis
from realty_lead_gen.models.property import Property
from realty_lead_gen.models.score import Persona, Score
from realty_lead_gen.schemas.buyer import SavedSearchCreate
from realty_lead_gen.utils.jsontypes import JSONDict

pytestmark = pytest.mark.unit

# Every model module, so `configure_mappers()` below sees the whole registry
# rather than whichever subset an earlier import happened to pull in.
_MODEL_PACKAGE = "realty_lead_gen.models"


def _import_all_models() -> None:
    package = importlib.import_module(_MODEL_PACKAGE)
    for module in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{_MODEL_PACKAGE}.{module.name}")


def test_alias_is_a_lazy_pep695_alias_over_dict_str_any() -> None:
    """The premise the rest of this module tests against."""
    # `TypeAliasType` is what the `type` statement produces. Asserting on it
    # by name rather than `isinstance(JSONDict, type)` because the whole
    # point is that it is *not* the `dict` class itself.
    assert type(JSONDict).__name__ == "TypeAliasType"
    assert JSONDict.__name__ == "JSONDict"

    value = JSONDict.__value__
    assert get_origin(value) is dict
    assert get_args(value) == (str, Any)


def test_sqlalchemy_resolves_the_alias_to_jsonb_columns() -> None:
    """`Mapped[JSONDict]` and `Mapped[list[JSONDict]]` still map to JSONB."""
    _import_all_models()
    # Raises if any annotation in the registry failed to resolve — including
    # a `Mapped[JSONDict]` SQLAlchemy could not see through.
    configure_mappers()

    # One plain `Mapped[JSONDict]` and one `Mapped[list[JSONDict]]`: the
    # nested form is the one that would break first, since it requires
    # unwrapping the alias from *inside* a generic.
    cases = [
        (Property, "attributes"),
        (Score, "components"),
        (DealAnalysis, "rehab_line_items"),
        (DealAnalysis, "comps"),
    ]
    for model, column_name in cases:
        column = sa_inspect(model).columns[column_name]
        assert isinstance(column.type, JSONB), f"{model.__name__}.{column_name}"
        assert column.nullable is False, f"{model.__name__}.{column_name}"


def test_pydantic_validates_through_the_alias() -> None:
    populated = SavedSearchCreate(
        name="s", persona=Persona.flipper, criteria={"max_price_cents": 1}
    )
    assert populated.criteria == {"max_price_cents": 1}

    # `default_factory=dict`, so an omitted field is an empty dict and not
    # a shared instance between models.
    first = SavedSearchCreate(name="a", persona=Persona.flipper)
    second = SavedSearchCreate(name="b", persona=Persona.flipper)
    assert first.criteria == {}
    assert first.criteria is not second.criteria

    with pytest.raises(ValueError, match="criteria"):
        SavedSearchCreate(
            name="s",
            persona=Persona.flipper,
            criteria="not-a-dict",  # type: ignore[arg-type]
        )


def test_openapi_emits_a_resolvable_object_schema() -> None:
    """The lazy alias becomes a named component, not a broken `$ref`.

    Pydantic inlines a `TypeAlias` but names a PEP 695 alias, so `criteria`
    and the `/matches` response body moved from an inline
    `{"type": "object", "additionalProperties": true}` to a `$ref` at a
    component carrying exactly that. Equivalent for any spec-compliant
    client and better for codegen — but it is a wire-visible difference, so
    it is asserted rather than assumed.
    """
    get_settings.cache_clear()
    # In-process quota store: building the app must not dial Redis.
    settings = get_settings().model_copy(update={"api_rate_limit_storage_uri": "async+memory://"})
    spec = create_app(settings).openapi()
    get_settings.cache_clear()

    components = spec["components"]["schemas"]
    assert components["JSONDict"] == {"type": "object", "additionalProperties": True}

    ref = {"$ref": "#/components/schemas/JSONDict"}
    assert components["SavedSearchCreate"]["properties"]["criteria"] == ref

    matches = spec["paths"]["/matches/property/{property_id}"]["get"]
    body = matches["responses"]["200"]["content"]["application/json"]["schema"]
    assert body["type"] == "array"
    assert body["items"] == ref
