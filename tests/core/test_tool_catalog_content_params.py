"""Descriptor ``content_params`` (bu-jv1moa.26 PR3): the arguments that carry delivered content.

Optional and additive: an undeclared tool publishes and hashes exactly as before; a declared one
is validated against its input schema and recipient parameters and changes its catalog digest.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from butlers.core.catalog_publication import decode_publication, publication
from butlers.core.tool_catalog import (
    ToolCatalog,
    ToolCatalogError,
    validate_authorization_catalog,
)
from butlers.core.tool_presentation_inventory import STORE_WRITERS, TOOL_PRESENTATION_BY_NAME

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


def test_the_inventory_declares_content_only_where_a_preview_needs_it() -> None:
    declared = {
        name: item.content_params
        for name, item in TOOL_PRESENTATION_BY_NAME.items()
        if item.content_params is not None
    }
    assert declared == {
        "telegram_edit_message_text": ("text",),
        "whatsapp_reply_to_message": ("text",),
    }


async def test_a_declared_content_param_validates_and_changes_the_digest() -> None:
    catalog = await _catalog()
    declared = _with(catalog, "notify", content_params=("message",))
    validate_authorization_catalog(declared, store_writers=STORE_WRITERS)
    assert declared["notify"].content_params == ("message",)


@pytest.mark.parametrize(
    "bad",
    [["message"], ("nope",), ("recipient",), ("message", "message"), (), (3,), ("",)],
    ids=["list", "not-an-argument", "is-a-recipient", "duplicate", "empty", "non-text", "blank"],
)
async def test_malformed_content_params_are_refused_naming_the_field(bad) -> None:
    catalog = _with(await _catalog(), "notify", content_params=bad)
    with pytest.raises(ToolCatalogError, match="notify.*content_params"):
        validate_authorization_catalog(catalog, store_writers=STORE_WRITERS)


async def test_an_undeclared_tool_publishes_without_the_key_and_keeps_its_digest() -> None:
    catalog = await _catalog()
    published = publication(catalog, "general")
    assert all("content_params" not in item for item in published["descriptors"])
    assert decode_publication(published, "general") == catalog


async def test_a_declared_tool_round_trips_through_publication_and_moves_the_digest() -> None:
    plain = await _catalog()
    declared = _with(plain, "notify", content_params=("message",))
    assert _digest_of(declared) != _digest_of(plain)
    declared = replace(declared, generation_digest=_digest_of(declared))
    published = publication(declared, "general")
    [item] = [i for i in published["descriptors"] if i["canonical_name"] == "notify"]
    assert item["content_params"] == ["message"]
    assert decode_publication(published, "general")["notify"].content_params == ("message",)


async def test_building_a_catalog_carries_content_params_and_moves_only_that_digest() -> None:
    from fastmcp import FastMCP

    from butlers.core.tool_catalog import build_tool_catalog
    from butlers.modules.base import ToolMeta

    async def build(content):
        mcp = FastMCP("content-params-test")

        @mcp.tool(description="Send a note.")
        async def send(to: str, body: str) -> str:
            return body

        kwargs = {} if content is None else {"content_params": content}
        return await build_tool_catalog(
            mcp,
            tool_owners={"send": "mail"},
            tool_metadata={
                "send": ToolMeta(
                    arg_sensitivities={"to": False, "body": False},
                    canonical_name="send",
                    module_name="mail",
                    group_name="write",
                    namespace="mail.write",
                    llm_presentable=True,
                    load_posture="deferred",
                    **kwargs,
                )
            },
        )

    plain, declared = await build(None), await build(("body",))
    assert plain["send"].content_params is None
    assert declared["send"].content_params == ("body",)
    assert plain.generation_digest != declared.generation_digest


def _digest_of(catalog: ToolCatalog) -> str:
    from dataclasses import fields

    from butlers.core.catalog_publication import OPTIONAL_FIELDS, generation, plain
    from butlers.core.tool_catalog import ToolDescriptor

    items = [
        {
            field.name: plain(getattr(item, field.name))
            for field in fields(ToolDescriptor)
            if field.name not in OPTIONAL_FIELDS or getattr(item, field.name) is not None
        }
        for item in sorted(catalog.descriptors, key=lambda item: item.canonical_name)
    ]
    return generation(items)
