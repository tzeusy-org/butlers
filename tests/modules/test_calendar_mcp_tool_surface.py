"""Pin the calendar module's MCP tool surface (bu-q11586.7).

The dashboard calendar workspace was narrowed to an operator view (owner decision
bu-kf3n4i = A), which deleted the dashboard's own Google-event edit routes. Butler and
chat calendar writes are a core butler function, so every MCP tool the calendar module
registers must stay registered and unchanged. This test pins the full registered set by
name so removing or renaming a tool fails loudly.
"""

from __future__ import annotations

import pytest

from butlers.modules.calendar import CalendarModule

pytestmark = pytest.mark.unit

EXPECTED_CALENDAR_TOOLS = frozenset(
    {
        "calendar_add_attendees",
        "calendar_create_butler_event",
        "calendar_create_event",
        "calendar_delete_butler_event",
        "calendar_delete_event",
        "calendar_delete_event_instance",
        "calendar_find_free_slots",
        "calendar_force_sync",
        "calendar_get_event",
        "calendar_list_calendars",
        "calendar_list_events",
        "calendar_propose_event",
        "calendar_remove_attendees",
        "calendar_set_primary",
        "calendar_sync_status",
        "calendar_toggle_butler_event",
        "calendar_update_butler_event",
        "calendar_update_event",
        "calendar_update_event_instance",
        "reminder_create",
        "reminder_dismiss",
        "reminder_list",
    }
)

# The write tools the dashboard no longer drives itself but butlers and chat still do.
WRITE_TOOLS = frozenset(
    {
        "calendar_create_event",
        "calendar_update_event",
        "calendar_delete_event",
    }
)


class _StubMCP:
    """Minimal MCP stub that captures registered tools by function name."""

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


async def _registered_tools() -> set[str]:
    mcp = _StubMCP()
    mod = CalendarModule()
    await mod.register_tools(
        mcp=mcp,
        config={"provider": "google", "calendar_id": "primary"},
        db=None,
        butler_name="test-butler",
    )
    return set(mcp.tools)


async def test_calendar_module_registers_the_full_tool_surface():
    assert await _registered_tools() == EXPECTED_CALENDAR_TOOLS


async def test_calendar_write_tools_stay_registered():
    assert WRITE_TOOLS <= await _registered_tools()
