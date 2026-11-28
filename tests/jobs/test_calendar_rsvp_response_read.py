"""The owner's own response is read unfiltered: a self-declined row still shows (H1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from butlers.api.db import DatabaseManager
from butlers.api.read_models.calendar_workspace import (
    query_owner_calendar_rows,
    query_owner_response_rows,
)
from butlers.jobs import calendar_rsvp_ledger as ledger
from tests.jobs.test_calendar_overcommitment import _row

_START = datetime(2026, 7, 2, 9, 0, tzinfo=UTC)


def _db(row: dict) -> MagicMock:
    db = MagicMock(spec=DatabaseManager)
    db.butler_names = ["general"]
    db.butlers_with_module = MagicMock(return_value=["general"])

    async def fan_out(query: str, args=(), butler_names=None):
        found = {"general": [row]} if "FROM calendar_event_instances AS i" in query else {}
        return found, []

    db.fan_out_with_status = AsyncMock(side_effect=fan_out)
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    db.pool = MagicMock(return_value=pool)
    return db


async def test_a_self_declined_row_is_in_the_response_read_but_not_the_time_read() -> None:
    row = _row("inst-1", "A", _START, _START)
    row["event_metadata"] = {
        "source_type": "provider_event",
        "attendees": [{"self": True, "response_status": "declined"}],
    }
    db = _db(row)
    window = {"start": _START - timedelta(hours=1), "end": _START + timedelta(days=1)}

    time_rows, time_ok = await query_owner_calendar_rows(db, **window)
    response_rows, response_ok = await query_owner_response_rows(db, **window)

    assert time_ok and response_ok
    assert time_rows == []  # a declined event holds no owner time
    assert ledger.owner_response(response_rows, "inst-1") == "declined"
