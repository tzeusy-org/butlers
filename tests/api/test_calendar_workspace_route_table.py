"""Route table of the dashboard calendar workspace (bu-q11586.7).

The workspace is an operator view (owner decision bu-kf3n4i = A): the dashboard's own
Google-event edit, find-time, dedup-review, ICS and undo surfaces are gone, while the
read, search, butler-lane, proposal, invitation, audit and source-settings routes stay.
"""

from __future__ import annotations

import functools

import pytest

from butlers.api.app import create_app

pytestmark = pytest.mark.unit

_W = "/api/calendar/workspace"

REMOVED = {
    (f"{_W}/duplicates", "get"),
    (f"{_W}/dedup-rules", "patch"),
    (f"{_W}/duplicates/keep-separate", "post"),
    (f"{_W}/find-time", "post"),
    (f"{_W}/undo/{{action_id}}", "post"),
    ("/api/calendar/export/ics", "get"),
    ("/api/calendar/subscribe.ics", "get"),
    ("/api/calendar/import/ics", "post"),
}

KEPT = {
    (f"{_W}", "get"),
    (f"{_W}/prep/{{event_id}}", "get"),
    (f"{_W}/day-briefing", "get"),
    (f"{_W}/invitations", "get"),
    (f"{_W}/conflicts", "get"),
    (f"{_W}/search", "get"),
    (f"{_W}/entries/{{entry_id}}", "get"),
    (f"{_W}/meta", "get"),
    (f"{_W}/primary", "put"),
    (f"{_W}/sync", "post"),
    (f"{_W}/parse-quick-add", "post"),
    (f"{_W}/responses/{{command_id}}", "get"),
    (f"{_W}/respond", "post"),
    (f"{_W}/user-events", "post"),
    (f"{_W}/butler-events", "post"),
    (f"{_W}/butler-events/preview", "post"),
    (f"{_W}/proposals/{{proposal_id}}/accept", "post"),
    (f"{_W}/proposals/{{proposal_id}}/dismiss", "post"),
    (f"{_W}/audit", "get"),
    ("/api/calendar/accounts", "get"),
    ("/api/calendar/sources", "post"),
}


@functools.cache
def _calendar_operations() -> frozenset[tuple[str, str]]:
    paths = create_app().openapi()["paths"]
    return frozenset(
        (path, method)
        for path, operations in paths.items()
        if path.startswith("/api/calendar")
        for method in operations
    )


def test_calendar_route_table_has_no_unexpected_routes():
    assert REMOVED.isdisjoint(KEPT)
    assert _calendar_operations() == KEPT
