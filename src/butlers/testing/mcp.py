"""Public FastMCP registration lookups for tests that call handlers directly."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP


async def registered_tools(mcp: FastMCP) -> dict[str, Callable[..., Any]]:
    """Return the currently registered functions under their public names.

    Calling these functions directly does not exercise MCP transport validation
    or authorization. Wrappers on the registered Tool remain in place.
    """
    return {tool.name: tool.fn for tool in await mcp.list_tools()}


async def tool_fn(mcp: FastMCP, name: str) -> Callable[..., Any]:
    """Look up the current registered function, refusing an unknown name."""
    tool = await mcp.get_tool(name)
    if tool is None:
        raise KeyError(name)
    return tool.fn
