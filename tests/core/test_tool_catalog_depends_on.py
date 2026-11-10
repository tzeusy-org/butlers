"""Descriptor ``depends_on`` (RFC 0038 S6): declared dependency IDs, validated."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastmcp import FastMCP

from butlers.core.capability_dependencies import DEPENDENCY_IDS
from butlers.core.tool_catalog import (
    AUTHORIZATION_FIELDS,
    ToolCatalog,
    ToolCatalogError,
    build_tool_catalog,
    validate_authorization_catalog,
)
from butlers.core.tool_presentation_inventory import STORE_WRITERS
from butlers.modules.base import ToolMeta

pytestmark = pytest.mark.unit


async def _catalog(depends_on: object = None) -> ToolCatalog:
    mcp = FastMCP("depends-on-test")

    @mcp.tool(description="Look up a value.")
    async def lookup(query: str) -> str:
        return query

    kwargs = {} if depends_on is None else {"depends_on": depends_on}
    return await build_tool_catalog(
        mcp,
        tool_owners={"lookup": "search"},
        tool_metadata={
            "lookup": ToolMeta(
                arg_sensitivities={"query": False},
                canonical_name="lookup",
                module_name="search",
                group_name="read",
                namespace="search.read",
                llm_presentable=True,
                load_posture="deferred",
                **kwargs,  # type: ignore[arg-type]
            )
        },
    )


def test_depends_on_is_an_authorization_field() -> None:
    assert "depends_on" in AUTHORIZATION_FIELDS


async def test_undeclared_depends_on_is_empty_and_changes_nothing() -> None:
    descriptor = (await _catalog())["lookup"]
    assert descriptor.depends_on == ()


async def test_declared_depends_on_is_carried_on_the_descriptor() -> None:
    declared = tuple(sorted(DEPENDENCY_IDS))[:2]
    descriptor = (await _catalog(declared))["lookup"]
    assert descriptor.depends_on == declared


async def test_depends_on_changes_the_catalog_digest() -> None:
    plain = await _catalog()
    declared = await _catalog((sorted(DEPENDENCY_IDS)[0],))
    assert plain.generation_digest != declared.generation_digest


async def _validated(descriptor_depends_on: object) -> None:
    from tests.contracts.test_tool_presentation_inventory import _core_catalog

    catalog = await _core_catalog("general", ("state", "notifications"))
    reader = catalog["state_get"]
    changed = replace(reader, depends_on=descriptor_depends_on)  # type: ignore[arg-type]
    validate_authorization_catalog(
        replace(
            catalog,
            descriptors=tuple(
                changed if item.canonical_name == "state_get" else item
                for item in catalog.descriptors
            ),
        ),
        store_writers=STORE_WRITERS,
    )


async def test_known_sorted_unique_depends_on_validates() -> None:
    await _validated(tuple(sorted(DEPENDENCY_IDS)[:2]))
    await _validated(())


@pytest.mark.parametrize(
    "bad",
    [
        None,
        ["google"],
        ("not_a_declared_dependency",),
        ("",),
        (7,),
        ("spotify", "spotify"),
        ("spotify", "google"),
    ],
    ids=["none", "list", "unknown", "empty", "non-string", "duplicate", "unsorted"],
)
async def test_malformed_depends_on_is_refused_naming_tool_and_field(bad: object) -> None:
    with pytest.raises(ToolCatalogError, match="state_get.*depends_on"):
        await _validated(bad)


async def test_depends_on_is_part_of_the_authorization_digest() -> None:
    from butlers.core.catalog_publication import authorization_digest

    plain = await _catalog()
    declared = await _catalog((sorted(DEPENDENCY_IDS)[0],))
    assert authorization_digest(plain) != authorization_digest(declared)
