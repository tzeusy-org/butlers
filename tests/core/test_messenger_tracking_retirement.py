"""Runtime-surface regressions for the Messenger tracking retirement."""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastmcp import FastMCP

from butlers.core_tools._base import ToolContext
from butlers.core_tools._messenger import register_messenger_tools
from butlers.core_tools._routing import _build_routed_delivery_command, register_routing_tools

ROOT = Path(__file__).resolve().parents[2]
_RETIRED_MCP_TOOLS = (
    "messenger_delivery_status",
    "messenger_delivery_search",
    "messenger_delivery_attempts",
    "messenger_delivery_trace",
    "messenger_dead_letter_list",
    "messenger_dead_letter_inspect",
    "messenger_dead_letter_replay",
    "messenger_dead_letter_discard",
    "messenger_validate_notify",
    "messenger_dry_run",
    "messenger_circuit_status",
    "messenger_rate_limit_status",
    "messenger_queue_depth",
    "messenger_delivery_stats",
)


def _messenger_mcp() -> tuple[FastMCP, ToolContext]:
    """Register the Messenger's real core surface on a FastMCP instance."""
    daemon = SimpleNamespace(db=None)
    context = ToolContext(
        daemon=daemon,
        pool=None,
        spawner=None,
        butler_name="messenger",
        butler_type=None,
        is_switchboard=False,
        is_messenger=True,
        route_metrics=MagicMock(),
    )
    mcp = FastMCP("messenger-retirement")

    def _core_tool(_group: str, **tool_kwargs):
        return mcp.tool(**tool_kwargs)

    register_routing_tools(context, mcp, _core_tool)
    register_messenger_tools(context, mcp, _core_tool)
    return mcp, context


async def test_actual_fastmcp_surface_keeps_live_egress_boundary_and_omits_retired_tools() -> None:
    """Retired names must be absent from the registry that dispatch resolves."""
    mcp, _ = _messenger_mcp()

    assert await mcp.get_tool("route.execute") is not None
    assert await mcp.get_tool("deferred_notifications_list") is not None
    for name in _RETIRED_MCP_TOOLS:
        assert await mcp.get_tool(name) is None, name


async def test_registered_route_execute_delegates_to_direct_channel_adapter_without_tracking_tables() -> (
    None
):
    """The registered runtime endpoint still leads to the native adapter command."""
    mcp, context = _messenger_mcp()
    route_execute = await mcp.get_tool("route.execute")
    assert route_execute is not None

    route_source = inspect.getsource(route_execute.fn)
    assert "_route_execute_inner" in route_source

    telegram = SimpleNamespace(_send_message=AsyncMock(return_value={"message_id": "42"}))
    command = await _build_routed_delivery_command(
        daemon=context.daemon,
        modules_by_name={"telegram": telegram},
        channel="telegram",
        intent="send",
        message_text="Reminder",
        origin="health",
        recipient="owner-chat",
        subject=None,
        notify_context=None,
    )
    assert command is not None
    assert command.tool_name == "telegram_send_message"
    assert command.tool_args == {"chat_id": "owner-chat", "text": "[health] Reminder"}
    await command.execute()
    telegram._send_message.assert_awaited_once_with("owner-chat", "[health] Reminder")


def test_retired_messenger_api_module_and_tracking_files_are_absent() -> None:
    assert not (ROOT / "roster/messenger/api/router.py").exists()
    assert not (ROOT / "roster/messenger/modules/__init__.py").exists()
    assert not (ROOT / "roster/messenger/tools/delivery/tracking.py").exists()
