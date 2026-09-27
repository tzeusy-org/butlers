from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError

import pytest
from fastmcp import FastMCP

from butlers.config import parse_approval_config
from butlers.core.tool_catalog import (
    ToolCatalog,
    ToolCatalogError,
    ToolPresentation,
    build_tool_catalog,
    validate_presentation_inventory,
    validate_tool_metadata,
)
from butlers.modules.approvals.gate import apply_approval_gates
from butlers.modules.base import ToolMeta

pytestmark = pytest.mark.unit


async def _catalog() -> ToolCatalog:
    mcp = FastMCP("catalog-test")

    @mcp.tool(description="Look up a value.")
    async def lookup(query: str) -> str:
        return query

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
            )
        },
    )


async def test_catalog_freezes_nested_definitions_and_stable_digests() -> None:
    first = await _catalog()
    second = await _catalog()
    descriptor = first["lookup"]

    assert first.generation_digest == second.generation_digest
    assert descriptor.input_schema_digest == second["lookup"].input_schema_digest
    assert descriptor.description_digest == second["lookup"].description_digest
    assert descriptor.arg_sensitivities == {"query": False}
    assert not hasattr(descriptor, "fn")
    with pytest.raises(TypeError):
        descriptor.input_schema["properties"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        descriptor.input_schema["properties"]["query"]["type"] = "number"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        descriptor.description = "changed"  # type: ignore[misc]


async def test_unclassified_registered_tool_uses_eager_presentable_compatibility() -> None:
    mcp = FastMCP("legacy")

    @mcp.tool()
    async def legacy_tool() -> None:
        pass

    catalog = await build_tool_catalog(mcp, tool_owners={"legacy_tool": "legacy"})

    assert catalog.classification_complete is False
    assert catalog["legacy_tool"].llm_presentable is True
    assert catalog["legacy_tool"].load_posture == "eager"
    assert catalog["legacy_tool"].classification_complete is False


def test_inventory_validation_rejects_duplicates_missing_stale_and_owner_drift() -> None:
    complete = (ToolPresentation("one", "alpha", "read", "alpha.read", True, "deferred"),)
    validate_presentation_inventory({"one": "alpha"}, complete)

    duplicate = (*complete, complete[0])
    with pytest.raises(ToolCatalogError, match="duplicate"):
        validate_presentation_inventory({"one": "alpha"}, duplicate)
    with pytest.raises(ToolCatalogError, match="missing"):
        validate_presentation_inventory({"one": "alpha", "two": "beta"}, complete)
    with pytest.raises(ToolCatalogError, match="stale"):
        validate_presentation_inventory({}, complete)
    with pytest.raises(ToolCatalogError, match="owner"):
        validate_presentation_inventory({"one": "beta"}, complete)


def test_invalid_or_partial_metadata_is_rejected() -> None:
    with pytest.raises(ValueError):
        ToolPresentation("one", "alpha", "read", "alpha.read", True, "sometimes")  # type: ignore[arg-type]
    with pytest.raises(ToolCatalogError, match="load posture"):
        validate_tool_metadata(
            "one",
            "alpha",
            ToolMeta(
                canonical_name="one",
                module_name="alpha",
                group_name="read",
                namespace="alpha.read",
                llm_presentable=True,
                load_posture="sometimes",  # type: ignore[arg-type]
            ),
        )
    with pytest.raises(ToolCatalogError, match="incomplete"):
        validate_tool_metadata("one", "alpha", ToolMeta(group_name="read"))


async def test_catalog_reads_post_approval_definition_without_retaining_handler() -> None:
    mcp = FastMCP("post-approval")
    calls = 0

    @mcp.tool(description="Send mail.")
    async def email_send_message(to: str, body: str) -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"to": to, "body": body}

    approval_config = parse_approval_config(
        {"enabled": True, "gated_tools": {"email_send_message": {}}}
    )
    await apply_approval_gates(mcp, approval_config, pool=object(), butler_name="messenger")
    catalog = await build_tool_catalog(
        mcp,
        tool_owners={"email_send_message": "email"},
        tool_metadata={
            "email_send_message": ToolMeta(
                arg_sensitivities={"to": True},
                canonical_name="email_send_message",
                module_name="email",
                group_name="write",
                namespace="email.write",
                llm_presentable=True,
                load_posture="deferred",
            )
        },
    )

    assert "_why" in catalog["email_send_message"].input_schema["properties"]
    assert catalog["email_send_message"].arg_sensitivities == {"to": True}
    assert calls == 0


async def test_concurrent_catalog_reads_share_one_stable_generation() -> None:
    catalog = await _catalog()

    async def read_generation():
        await asyncio.sleep(0)
        return catalog.generation_digest, catalog["lookup"]

    reads = await asyncio.gather(*(read_generation() for _ in range(20)))

    assert {digest for digest, _descriptor in reads} == {catalog.generation_digest}
    assert all(descriptor is catalog["lookup"] for _digest, descriptor in reads)
