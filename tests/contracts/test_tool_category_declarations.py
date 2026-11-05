"""Every catalog tool declares its S1 data categories, cited to its domain spec.

REQ-capability-authorization-019 / RFC 0038 S1: the descriptor carries
``categories``, a set of the six names, and missing metadata is ungrantable
(bu-dhloi1.27). The checked-in inventory is the declaration; these tests pin
that nothing reaches the catalog undeclared and that each declaration names
the spec it was read from.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from butlers.core.goal_labels import CATEGORIES
from butlers.core.tool_catalog import ToolCatalogError, validate_authorization_catalog
from butlers.core.tool_presentation_inventory import (
    ALL_CATEGORIES,
    CATEGORY_SPECS,
    STORE_WRITERS,
    TOOL_PRESENTATION_BY_NAME,
    TOOL_PRESENTATION_INVENTORY,
    _declare,
    category_spec,
)

REPO = Path(__file__).resolve().parents[2]


def test_every_inventory_declaration_names_a_subset_of_the_six_categories():
    for declaration in TOOL_PRESENTATION_INVENTORY:
        categories = declaration.categories
        assert isinstance(categories, tuple), declaration.canonical_name
        assert set(categories) <= CATEGORIES, declaration.canonical_name
        assert list(categories) == sorted(set(categories)), declaration.canonical_name


def test_every_declaration_cites_an_existing_domain_spec():
    for declaration in TOOL_PRESENTATION_INVENTORY:
        specs = category_spec(declaration.module_name, declaration.group_name)
        assert specs, declaration.canonical_name
        for spec in specs:
            assert (REPO / spec).is_file(), (declaration.canonical_name, spec)
    for key, specs in CATEGORY_SPECS.items():
        assert specs and all((REPO / spec).is_file() for spec in specs), key


def test_a_declaration_without_categories_does_not_build():
    with pytest.raises(TypeError, match="categories"):
        _declare("core", "state", "state_get", effect_class="read")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="categories"):
        _declare("core", "state", "state_get", categories=("horoscope",))


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # Free text or cross-domain content can carry any category.
        ("email_read_message", ALL_CATEGORIES),
        ("state_get", ALL_CATEGORIES),
        ("calendar_list_events", ALL_CATEGORIES),
        ("memory_search", ALL_CATEGORIES),
        ("note_list", ALL_CATEGORIES),
        ("ha_get_entity_state", ALL_CATEGORIES),
    ],
)
def test_declared_categories_follow_the_domain_data(name, expected):
    assert TOOL_PRESENTATION_BY_NAME[name].categories == expected


async def test_the_catalog_gate_refuses_a_presentable_tool_without_categories():
    from tests.contracts.test_tool_presentation_inventory import _core_catalog

    catalog = await _core_catalog("general", ("state", "notifications"))
    reader = catalog["state_get"]
    assert reader.categories == ALL_CATEGORIES and reader.grantable
    missing = replace(reader, categories=None)
    assert not missing.authorization_complete and not missing.grantable
    changed = replace(
        catalog,
        descriptors=tuple(
            missing if item.canonical_name == "state_get" else item for item in catalog.descriptors
        ),
    )
    with pytest.raises(ToolCatalogError, match="state_get.*categories"):
        validate_authorization_catalog(changed, store_writers=STORE_WRITERS)
