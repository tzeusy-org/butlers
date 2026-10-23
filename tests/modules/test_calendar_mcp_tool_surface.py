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

from butlers.modules.calendar import CalendarModule

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
        "calendar_scan_reminder_events",
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


class _StubMCP:
    """Minimal MCP stub that captures registered tools by function name."""

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, *args, **kwargs):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


async def _registered_tools(groups: list[str] | None) -> set[str]:
    mcp = _StubMCP()
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
    return set(mcp.tools)


@pytest.mark.parametrize(
    "groups",
    [None, [], ["core", "butler_events", "attendees"]],
    ids=["default", "empty", "explicit-including-retired"],
)
async def test_calendar_module_registers_the_full_tool_surface(groups):
    assert await _registered_tools(groups) == EXPECTED_CALENDAR_TOOLS


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
