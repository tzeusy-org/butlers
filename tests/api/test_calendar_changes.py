"""GET /api/calendar/workspace/changes: organizer-made changes, honest when unreadable (N3)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import MCPClientManager, get_db, get_mcp_manager

pytestmark = pytest.mark.unit

_NOW = datetime.now(UTC).replace(microsecond=0)
_START = _NOW + timedelta(days=2)


def _journal_row(event_id="evt-1", *, organizer_self=False, prior_known=True, **over):
    row = {
        "event_id": event_id,
        "calendar_id": "primary",
        "observed_at": _NOW - timedelta(minutes=5),
        "prior_known": prior_known,
        "all_day": False,
        "prior_start": _START,
        "new_start": _START + timedelta(hours=2),
        "prior_end": _START + timedelta(hours=1),
        "new_end": _START + timedelta(hours=3),
        "prior_status": "confirmed",
        "new_status": "confirmed",
        "prior_location": "Room 1",
        "new_location": "Room 1",
        "organizer_self": organizer_self,
    }
    row.update(over)
    return row


def _build(app, *, journal=None, journal_error=None, titles=None, emails=None):
    db = MagicMock(spec=DatabaseManager)
    db.butler_names = ["general"]
    switchboard = MagicMock()
    if journal_error is not None:
        switchboard.fetch = AsyncMock(side_effect=journal_error)
    else:
        switchboard.fetch = AsyncMock(return_value=journal or [])
    db.pool = MagicMock(return_value=switchboard)

    async def _fan_out(query, args=(), butler_names=None, deadline_seconds=None):
        if "account_email" in query:
            return {"general": [{"email": e} for e in (emails or [])]}, []
        return {"general": titles or []}, []

    db.fan_out_with_status = AsyncMock(side_effect=_fan_out)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_mcp_manager] = lambda: AsyncMock(spec=MCPClientManager)
    return app, db, switchboard


async def _get(app, since=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        params = {"since": since} if since is not None else {}
        return await client.get("/api/calendar/workspace/changes", params=params)


def _since(hours=1):
    return (_NOW - timedelta(hours=hours)).isoformat()


async def test_calendar_changes_returns_before_and_after_with_the_projection_title(app):
    instance = uuid.uuid4()
    app, db, switchboard = _build(
        app,
        journal=[_journal_row()],
        titles=[
            {
                "origin_ref": "evt-1",
                "origin_instance_ref": "evt-1",
                "title": "Planning",
                "instance_id": instance,
                "starts_at": _START + timedelta(hours=2),
                "quarantined": False,
            }
        ],
    )

    resp = await _get(app, _since())

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["changes_available"] is True
    (change,) = data["changes"]
    assert change["title"] == "Planning" and change["entry_id"] == str(instance)
    assert change["fields"] == ["time"]
    assert change["prior_start_at"] != change["new_start_at"]
    assert change["organizer_self"] is False
    # Only the narrow reader is asked, on the Switchboard pool, with the cap plus one.
    db.pool.assert_called_with("switchboard")
    assert "read_calendar_event_changes" in switchboard.fetch.await_args.args[0]
    assert switchboard.fetch.await_args.args[2] == 201


async def test_calendar_changes_an_unreadable_journal_is_unknown_not_empty(app):
    app, _, _ = _build(app, journal_error=RuntimeError("connectors unreadable"))

    resp = await _get(app, _since())

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["changes_available"] is False and data["changes"] == []


async def test_calendar_changes_an_unknown_organizer_is_returned_marked_unknown(app):
    app, _, _ = _build(
        app, journal=[_journal_row(organizer_self=None, prior_location=None, new_location=None)]
    )

    resp = await _get(app, _since())

    (change,) = resp.json()["data"]["changes"]
    assert change["organizer_self"] is None and change["title"] is None
    assert change["entry_id"] is None


async def test_calendar_changes_a_change_with_no_prior_version_names_no_fields(app):
    app, _, _ = _build(app, journal=[_journal_row(prior_known=False, prior_start=None)])

    resp = await _get(app, _since())

    (change,) = resp.json()["data"]["changes"]
    assert change["prior_known"] is False and change["fields"] == []


async def test_calendar_changes_truncation_is_reported(app):
    app, _, _ = _build(app, journal=[_journal_row(f"evt-{i}") for i in range(201)])

    resp = await _get(app, _since())

    data = resp.json()["data"]
    assert data["truncated"] is True and len(data["changes"]) == 200


@pytest.mark.parametrize("since_label", ["fifteen_days_ago", "start_of_year"])
async def test_calendar_changes_refuses_a_window_it_would_have_to_shorten(app, since_label):
    # Computed in the test: a wall-clock value in the parametrize id differs between xdist workers.
    since = {
        "fifteen_days_ago": (_NOW - timedelta(days=15)).isoformat(),
        "start_of_year": "2026-01-01T00:00:00",
    }[since_label]
    app, _, switchboard = _build(app)

    resp = await _get(app, since)

    assert resp.status_code == 400
    switchboard.fetch.assert_not_awaited()


async def test_calendar_changes_a_quarantined_or_unprojected_event_shows_no_third_party_text(app):
    quarantined = uuid.uuid4()
    app, _, _ = _build(
        app,
        journal=[_journal_row("evt-q", new_location="Lure Room"), _journal_row("evt-u")],
        titles=[
            {
                "origin_ref": "evt-q",
                "origin_instance_ref": "evt-q",
                "title": "Free prize",
                "instance_id": quarantined,
                "starts_at": _START + timedelta(hours=2),
                "quarantined": True,
            }
        ],
    )

    resp = await _get(app, _since())

    for change in resp.json()["data"]["changes"]:
        assert change["title"] is None and change["entry_id"] is None
        assert change["prior_location"] is None and change["new_location"] is None
        assert change["new_start_at"] is not None  # the move itself is still shown


async def test_calendar_changes_defaults_to_seven_days_and_passes_the_owners_addresses(app):
    app, _, switchboard = _build(
        app, journal=[], emails=["me@example.test", "me.work@example.test"]
    )

    before = datetime.now(UTC)
    resp = await _get(app)
    after = datetime.now(UTC)

    assert resp.status_code == 200
    args = switchboard.fetch.await_args.args
    # exactly seven days before the moment the route read the clock, which lies between the two
    assert before - timedelta(days=7) <= args[1] <= after - timedelta(days=7)
    assert args[3] == ["me.work@example.test", "me@example.test"]


async def test_calendar_changes_without_the_owners_addresses_still_answers(app):
    app, db, _ = _build(app, journal=[_journal_row()])
    db.fan_out_with_status = AsyncMock(side_effect=RuntimeError("sources unreadable"))

    resp = await _get(app, _since())

    assert resp.status_code == 200 and len(resp.json()["data"]["changes"]) == 1


async def test_calendar_changes_a_cursor_from_a_clock_ahead_reads_as_now(app):
    app, _, switchboard = _build(app, journal=[])

    resp = await _get(app, (datetime.now(UTC) + timedelta(hours=2)).isoformat())

    assert resp.status_code == 200
    assert switchboard.fetch.await_args.args[1] <= datetime.now(UTC)


async def test_calendar_changes_serves_an_age_a_few_minutes_past_the_bound(app):
    app, _, _ = _build(app, journal=[])

    resp = await _get(app, (datetime.now(UTC) - timedelta(days=14, minutes=3)).isoformat())

    assert resp.status_code == 200


async def test_calendar_changes_unreadable_owner_addresses_make_the_result_a_lower_bound(app):
    app, db, _ = _build(app, journal=[_journal_row()])
    db.fan_out_with_status = AsyncMock(return_value=({}, ["general"]))

    data = (await _get(app, _since())).json()["data"]

    assert data["owners_complete"] is False and len(data["changes"]) == 1
