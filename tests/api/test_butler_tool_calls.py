"""The one dashboard parser for butler MCP tool results (bu-2c1g7v PR 3a, bu-16ssys)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastmcp.client.client import CallToolResult
from mcp.types import ImageContent, TextContent

from butlers.api.butler_tool_calls import (
    parse_tool_text,
    tool_result_is_error,
    tool_result_payload,
    tool_result_text,
)

pytestmark = pytest.mark.unit


def _result(*texts: str, is_error: bool = False, structured=None) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=text) for text in texts],
        structured_content=structured,
        meta=None,
        is_error=is_error,
    )


def test_a_json_text_block_parses_to_its_payload() -> None:
    payload = {"id": "s1", "status": "triggered"}

    assert tool_result_payload(_result(json.dumps(payload))) == payload


def test_plain_text_stays_text_and_no_text_is_none() -> None:
    assert tool_result_payload(_result("not json")) == "not json"
    assert tool_result_payload(_result()) is None
    assert tool_result_payload(_result("")) is None


def test_text_blocks_join_and_non_text_blocks_are_skipped() -> None:
    image = ImageContent(type="image", data="aGk=", mimeType="image/png")
    result = CallToolResult(
        content=[TextContent(type="text", text="a"), image, TextContent(type="text", text="b")],
        structured_content=None,
        meta=None,
    )

    assert tool_result_text(result) == "a\nb"


def test_structured_content_never_substitutes_for_text() -> None:
    """Only the text the tool returned is parsed; the SDK object is never stringified."""
    result = _result(structured={"status": "created"})

    assert tool_result_payload(result) is None


def test_a_legacy_content_list_parses_like_a_result() -> None:
    blocks = [SimpleNamespace(text=json.dumps({"ok": True}))]

    assert tool_result_payload(blocks) == {"ok": True}


def test_an_unrecognised_object_has_no_text() -> None:
    assert tool_result_text(object()) is None
    assert tool_result_text(SimpleNamespace(content="not a list")) is None


def test_error_flag_comes_only_from_the_sdk_result() -> None:
    assert tool_result_is_error(_result("{}", is_error=True))
    assert not tool_result_is_error(_result(json.dumps({"status": "error"})))
    assert not tool_result_is_error([SimpleNamespace(text="{}")])


@pytest.mark.parametrize(("raw", "parsed"), [(None, None), ("[1]", [1]), ("{", "{")])
def test_parse_tool_text(raw, parsed) -> None:
    assert parse_tool_text(raw) == parsed
