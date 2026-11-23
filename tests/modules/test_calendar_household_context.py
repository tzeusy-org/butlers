"""Free-slot search lists other people's events as opaque context (REQ-module-calendar-038)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from butlers.modules.calendar import CalendarModule

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)


def _row(role="household_member", **extra):
    return {
        "time_role": role,
        "metadata": {"attendees": [{"email": "guest@example.test"}]},
        "starts_at": _T0,
        "ends_at": _T0 + timedelta(hours=1),
        "all_day": False,
        "source_id": uuid4(),
        "subject_entity_id": uuid4(),
        "subject_label": "Alex",
        "event_type": "default",
        **extra,
    }


def _host(rows=None, error=None):
    fetch = AsyncMock(side_effect=error) if error else AsyncMock(return_value=rows)
    return SimpleNamespace(_db=SimpleNamespace(pool=SimpleNamespace(fetch=fetch)))


async def test_household_events_are_listed_with_time_and_subject_only():
    host = _host(
        [_row(), _row("unclassified"), _row("team", metadata={"transparency": "transparent"})]
    )
    result = await CalendarModule._household_context(host, _T0, _T0 + timedelta(days=1))
    assert [set(r) for r in result] == [
        {"role", "subject_entity_id", "subject_label", "start_at", "end_at", "all_day"}
    ]
    assert result[0]["subject_label"] == "Alex"
    assert "guest@example.test" not in repr(result)


async def test_an_unreadable_ledger_gives_no_context_rather_than_an_error():
    host = _host(error=RuntimeError("down"))
    assert await CalendarModule._household_context(host, _T0, _T0 + timedelta(days=1)) == []


async def test_without_a_pool_there_is_no_context():
    host = SimpleNamespace(_db=None)
    assert await CalendarModule._household_context(host, _T0, _T0 + timedelta(days=1)) == []
