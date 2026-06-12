"""Tests for MCP wrapper tool-call capture metadata."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from butlers.mcp_wrappers import _ToolCallLoggingMCP

pytestmark = pytest.mark.unit


async def test_tool_call_capture_fingerprints_hidden_arguments() -> None:
    """Full inputs affect loop signatures without persisting raw non-allowlisted fields."""

    def tool_decorator(*_args: Any, **_kwargs: Any):
        def decorator(fn):
            return fn

        return decorator

    mock_mcp = MagicMock()
    mock_mcp.tool = tool_decorator
    proxy = _ToolCallLoggingMCP(mock_mcp, "relationship", module_name="relationship")

    @proxy.tool(name="contact_resolve")
    async def contact_resolve(name: str, context: str | None = None) -> dict[str, Any]:
        return {"contact_id": None, "confidence": "none", "candidates": []}

    with patch("butlers.mcp_wrappers.capture_tool_call") as capture:
        await contact_resolve(name="Person A")
        await contact_resolve(name="Person B")

    first = capture.call_args_list[0].kwargs
    second = capture.call_args_list[1].kwargs
    assert first["input_payload"] == {}
    assert second["input_payload"] == {}
    assert first["input_fingerprint"] != second["input_fingerprint"]
