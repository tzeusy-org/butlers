"""Unit tests for the Education source-material registry.

Requirement: REQ-education-source-grounding-001.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


SOURCE_KEY_PREFIX = "education/source/"


def _source_value(**overrides: Any) -> dict[str, Any]:
    value = {
        "title": "Structure and Interpretation of Computer Programs",
        "authors": ["Harold Abelson", "Gerald Jay Sussman"],
        "type": "book",
        "registered_at": "2026-08-21T00:00:00+00:00",
    }
    value.update(overrides)
    return value


async def test_register_source_persists_metadata_under_generated_uuid() -> None:
    from butlers.tools.education.source_material import source_material_register

    pool = AsyncMock()
    with patch(
        "butlers.tools.education.source_material.state_set", new_callable=AsyncMock
    ) as state_set:
        result = await source_material_register(
            pool,
            title="Structure and Interpretation of Computer Programs",
            authors=["Harold Abelson", "Gerald Jay Sussman"],
            type="book",
            toc=[{"title": "Building Abstractions", "location": "chapter 1"}],
            url="https://example.test/sicp",
        )

    source_id = result["source_id"]
    assert result["title"] == "Structure and Interpretation of Computer Programs"
    assert result["authors"] == ["Harold Abelson", "Gerald Jay Sussman"]
    assert result["type"] == "book"
    assert result["toc"] == [{"title": "Building Abstractions", "location": "chapter 1"}]
    assert result["url"] == "https://example.test/sicp"
    assert result["registered_at"]

    state_set.assert_awaited_once()
    key, value = state_set.await_args.args[1:]
    assert key == f"{SOURCE_KEY_PREFIX}{source_id}"
    assert value == {
        "title": "Structure and Interpretation of Computer Programs",
        "authors": ["Harold Abelson", "Gerald Jay Sussman"],
        "type": "book",
        "toc": [{"title": "Building Abstractions", "location": "chapter 1"}],
        "url": "https://example.test/sicp",
        "registered_at": result["registered_at"],
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"title": "  ", "type": "book"}, "title"),
        ({"title": "A source", "type": "  "}, "type"),
        ({"title": "A source", "type": "video"}, "one of"),
    ],
)
async def test_register_source_validates_required_and_supported_fields(
    kwargs: dict[str, Any], message: str
) -> None:
    from butlers.tools.education.source_material import source_material_register

    pool = AsyncMock()
    with patch(
        "butlers.tools.education.source_material.state_set", new_callable=AsyncMock
    ) as state_set:
        with pytest.raises(ValueError, match=message):
            await source_material_register(pool, **kwargs)

    state_set.assert_not_awaited()


async def test_list_source_returns_source_ids_and_preserves_metadata() -> None:
    from butlers.tools.education.source_material import source_material_list

    source_id = "6f31cb42-09f9-4ca6-8e24-3dc2c1d0c2fd"
    pool = AsyncMock()
    with patch(
        "butlers.tools.education.source_material.state_list", new_callable=AsyncMock
    ) as state_list:
        state_list.return_value = [
            {"key": f"{SOURCE_KEY_PREFIX}{source_id}", "value": _source_value(toc=["chapter 1"])}
        ]
        result = await source_material_list(pool)

    assert result == [{"source_id": source_id, **_source_value(toc=["chapter 1"])}]
    state_list.assert_awaited_once_with(pool, prefix=SOURCE_KEY_PREFIX, keys_only=False)


async def test_remove_source_deletes_only_registry_key_and_leaves_dangling_refs() -> None:
    from butlers.tools.education.source_material import source_material_remove

    source_id = "6f31cb42-09f9-4ca6-8e24-3dc2c1d0c2fd"
    pool = AsyncMock()
    with patch(
        "butlers.tools.education.source_material.state_delete", new_callable=AsyncMock
    ) as state_delete:
        result = await source_material_remove(pool, source_id)

    assert result == {"source_id": source_id, "status": "removed"}
    state_delete.assert_awaited_once_with(pool, f"{SOURCE_KEY_PREFIX}{source_id}")
    pool.fetch.assert_not_awaited()
    pool.fetchrow.assert_not_awaited()
    pool.execute.assert_not_awaited()


async def test_education_module_registers_source_material_tools() -> None:
    from roster.education.modules import EducationModule, EducationModuleConfig

    class _FakeMCP:
        def __init__(self) -> None:
            self.tools: dict[str, Any] = {}

        def tool(self):
            def decorator(fn):
                self.tools[fn.__name__] = fn
                return fn

            return decorator

    mcp = _FakeMCP()
    module = EducationModule()
    await module.register_tools(
        mcp,
        EducationModuleConfig(),
        SimpleNamespace(pool=AsyncMock()),
        "education",
    )

    assert {
        "source_material_register",
        "source_material_list",
        "source_material_remove",
    } <= mcp.tools.keys()


async def test_source_material_register_schema_requires_title_and_type() -> None:
    from fastmcp import FastMCP

    from roster.education.modules import EducationModule, EducationModuleConfig

    mcp = FastMCP("source-material-schema")
    module = EducationModule()
    await module.register_tools(
        mcp,
        EducationModuleConfig(),
        SimpleNamespace(pool=AsyncMock()),
        "education",
    )

    tool = await mcp.get_tool("source_material_register")

    assert set(tool.parameters["required"]) >= {"title", "type"}
