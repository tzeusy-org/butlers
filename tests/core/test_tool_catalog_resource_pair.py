"""Descriptor ``resource_pair`` (bu-jv1moa.64): two resource params scoped as one pair.

Optional and additive like ``content_params``: an undeclared tool publishes and hashes exactly as
before; a declared one is validated and changes its catalog digest.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from butlers.core.catalog_publication import decode_publication, generation, plain, publication
from butlers.core.tool_catalog import (
    ToolCatalog,
    ToolCatalogError,
    ToolDescriptor,
    validate_authorization_catalog,
)
from butlers.core.tool_presentation_inventory import STORE_WRITERS

pytestmark = pytest.mark.unit


async def _catalog() -> ToolCatalog:
    from tests.contracts.test_tool_presentation_inventory import _core_catalog

    return await _core_catalog("general", ("state", "notifications"))


def _with(catalog: ToolCatalog, name: str, **changes) -> ToolCatalog:
    return replace(
        catalog,
        descriptors=tuple(
            replace(item, **changes) if item.canonical_name == name else item
            for item in catalog.descriptors
        ),
    )


def _pair_of(catalog: ToolCatalog) -> tuple[str, str]:
    descriptor = catalog["notify"]
    assert len(descriptor.resource_params) >= 2
    return descriptor.resource_params[0], descriptor.resource_params[1]


async def test_a_declared_pair_validates() -> None:
    catalog = await _catalog()
    declared = _with(catalog, "notify", resource_pair=_pair_of(catalog))
    validate_authorization_catalog(declared, store_writers=STORE_WRITERS)


@pytest.mark.parametrize(
    "bad",
    [
        ["entity_id", "request_context"],
        ("entity_id",),
        ("entity_id", "entity_id"),
        ("entity_id", "nope"),
        ("entity_id", 3),
        ("entity_id", "request_context", "recipient"),
    ],
    ids=["list", "short", "same", "not-a-resource", "non-text", "long"],
)
async def test_a_malformed_pair_is_refused_naming_the_field(bad) -> None:
    catalog = _with(await _catalog(), "notify", resource_pair=bad)
    with pytest.raises(ToolCatalogError, match="notify.*resource_pair"):
        validate_authorization_catalog(catalog, store_writers=STORE_WRITERS)


async def test_a_selection_bound_param_cannot_join_a_pair() -> None:
    catalog = await _catalog()
    first, second = _pair_of(catalog)
    bound = {name: name == first for name in catalog["notify"].resource_params}
    broken = _with(catalog, "notify", resource_pair=(first, second), selection_bound=bound)
    with pytest.raises(ToolCatalogError, match="notify.*resource_pair"):
        validate_authorization_catalog(broken, store_writers=STORE_WRITERS)


async def test_an_undeclared_tool_publishes_without_the_key_and_keeps_its_digest() -> None:
    catalog = await _catalog()
    published = publication(catalog, "general")
    assert all("resource_pair" not in item for item in published["descriptors"])
    assert decode_publication(published, "general") == catalog


async def test_a_declared_tool_round_trips_and_moves_the_digest() -> None:
    plain_catalog = await _catalog()
    declared = _with(plain_catalog, "notify", resource_pair=_pair_of(plain_catalog))
    declared = replace(declared, generation_digest=_digest_of(declared))
    assert declared.generation_digest != _digest_of(plain_catalog)
    published = publication(declared, "general")
    [item] = [i for i in published["descriptors"] if i["canonical_name"] == "notify"]
    assert item["resource_pair"] == list(_pair_of(plain_catalog))
    decoded = decode_publication(published, "general")
    assert decoded["notify"].resource_pair == _pair_of(plain_catalog)


def _digest_of(catalog: ToolCatalog) -> str:
    from dataclasses import fields

    from butlers.core.catalog_publication import OPTIONAL_FIELDS

    return generation(
        [
            {
                field.name: plain(getattr(item, field.name))
                for field in fields(ToolDescriptor)
                if field.name not in OPTIONAL_FIELDS or getattr(item, field.name) is not None
            }
            for item in sorted(catalog.descriptors, key=lambda item: item.canonical_name)
        ]
    )
