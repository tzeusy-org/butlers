"""Real public registration and current-handler lookup, without a transport."""

from functools import wraps

import pytest
from fastmcp import FastMCP

from butlers.testing.mcp import registered_tools, tool_fn

pytestmark = pytest.mark.unit


async def test_declared_names_unknown_lookup_and_instance_isolation():
    first, second = FastMCP(), FastMCP()

    async def implementation(value: str) -> str:
        return value

    first.tool(name="declared_name")(implementation)
    assert await registered_tools(first) == {"declared_name": implementation}
    assert await tool_fn(first, "declared_name") is implementation
    assert await (await tool_fn(first, "declared_name"))("value") == "value"
    assert await registered_tools(second) == {}
    for server, name in [(first, "implementation"), (second, "declared_name")]:
        with pytest.raises(KeyError) as error:
            await tool_fn(server, name)
        assert error.value.args == (name,)


async def test_current_wrapped_handler_refuses_then_observes_replacement():
    server = FastMCP()

    async def original(value: str) -> str:
        return value

    @wraps(original)
    async def denied(value: str) -> str:
        raise PermissionError("closed test gate")

    server.tool(name="gated")(denied)
    assert await tool_fn(server, "gated") is denied
    assert (await registered_tools(server))["gated"] is denied
    with pytest.raises(PermissionError, match="closed test gate"):
        await (await tool_fn(server, "gated"))("value")

    tool = await server.get_tool("gated")
    assert tool is not None
    tool.fn = original
    assert await tool_fn(server, "gated") is original
    assert (await registered_tools(server))["gated"] is original
    assert await (await tool_fn(server, "gated"))("value") == "value"
