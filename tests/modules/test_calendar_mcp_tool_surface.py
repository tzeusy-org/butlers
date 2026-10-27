"""Pin the calendar module's MCP tool surface (bu-q11586.7).

The dashboard calendar workspace was narrowed to an operator view (owner decision
bu-kf3n4i = A), which deleted the dashboard's own Google-event edit routes. Butler and
chat calendar writes are a core butler function. The separately accepted bu-qozovd.1
slice 1 retires only attendee add/remove. This test pins every remaining registered
name across default and explicit group configurations (REQ-module-calendar-031).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastmcp import FastMCP

from butlers.modules.calendar import CalendarModule
from butlers.testing.mcp import registered_tools

pytestmark = pytest.mark.unit

# Includes provider writes retained for butlers/chat and approval-gated invitation answers.
EXPECTED_CALENDAR_TOOLS = frozenset(
    {
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
        "calendar_respond",
        "calendar_response_fence_status",
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


async def _registered_tools(groups: list[str] | None) -> set[str]:
    mcp = FastMCP("calendar-test-tools")
    mod = CalendarModule()
    config = {"provider": "google", "calendar_id": "primary", "response_enabled": True}
    if groups is not None:
        config["groups"] = groups
    await mod.register_tools(
        mcp=mcp,
        config=config,
        db=None,
        butler_name="messenger",
    )
    return set(await registered_tools(mcp))


@pytest.mark.parametrize(
    ("groups", "expected"),
    [
        (None, EXPECTED_CALENDAR_TOOLS),
        ([], EXPECTED_CALENDAR_TOOLS),
        (["core", "butler_events", "attendees"], None),
        (["core", "butler_events", "attendees", "response"], EXPECTED_CALENDAR_TOOLS),
        (["response"], {"calendar_respond", "calendar_response_fence_status"}),
    ],
    ids=["default", "empty", "explicit-missing-response", "explicit-all", "response-only"],
)
async def test_calendar_module_registers_the_full_tool_surface(groups, expected):
    # REQ-module-calendar-032: explicit groups select response independently.
    if expected is None:
        with pytest.raises(RuntimeError, match="response registration group"):
            await _registered_tools(groups)
    else:
        assert await _registered_tools(groups) == expected


def test_calendar_package_source_units_stay_bounded():
    """The accepted mechanical split keeps source and registration readable."""
    package = Path(inspect.getfile(CalendarModule)).parent
    assert Path(inspect.getfile(CalendarModule)).name == "__init__.py"
    sizes = {
        str(path.relative_to(package)): len(path.read_text().splitlines())
        for path in package.rglob("*.py")
    }
    assert sizes and max(sizes.values()) <= 2_000, sizes
    assert len(inspect.getsource(CalendarModule.register_tools).splitlines()) <= 300
