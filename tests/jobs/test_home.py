"""Unit and integration tests for butlers.jobs.home — maintenance schedule check job.

Covers:
- classify_item: all severity branches (due, overdue, critical, upcoming, never_completed, ok)
- build_notification_text: grouping, ordering, message content
- run_maintenance_schedule_check: query logic, classification, return values,
  notification delivery, no-action path, DB error propagation

All tests use mocked asyncpg pools — no real database required.

Issue: bu-smht
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.jobs.home import (
    SEVERITY_CRITICAL,
    SEVERITY_DUE,
    SEVERITY_NEVER_COMPLETED,
    SEVERITY_OVERDUE,
    SEVERITY_UPCOMING,
    MaintenanceItemRow,
    build_notification_text,
    classify_item,
    run_maintenance_schedule_check,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)


def _make_item(
    *,
    id: str = "test-id-1",
    name: str = "Air Filter",
    category: str = "filter",
    interval_days: int = 90,
    last_completed_at: datetime | None = None,
    next_due_at: datetime | None = None,
    notes: str | None = None,
) -> MaintenanceItemRow:
    return MaintenanceItemRow(
        id=id,
        name=name,
        category=category,
        interval_days=interval_days,
        last_completed_at=last_completed_at,
        next_due_at=next_due_at,
        notes=notes,
    )


def _make_pool(*, fetch_rows: list[Any] | None = None) -> MagicMock:
    """Return a minimal mock asyncpg pool for job tests."""
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=fetch_rows or [])
    pool.execute = AsyncMock()
    return pool


def _make_db_row(
    *,
    id: str = "test-id-1",
    name: str = "Air Filter",
    category: str = "filter",
    interval_days: int = 90,
    last_completed_at: datetime | None = None,
    next_due_at: datetime | None = None,
    notes: str | None = None,
) -> MagicMock:
    """Simulate an asyncpg record row from home.maintenance_items."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": id,
        "name": name,
        "category": category,
        "interval_days": interval_days,
        "last_completed_at": last_completed_at,
        "next_due_at": next_due_at,
        "notes": notes,
    }[key]
    return row


# ---------------------------------------------------------------------------
# classify_item tests
# ---------------------------------------------------------------------------


class TestClassifyItem:
    def test_never_completed(self):
        """Items with no last_completed_at and no next_due_at are never_completed."""
        item = _make_item(last_completed_at=None, next_due_at=None)
        result = classify_item(item, now=_NOW)
        assert result is not None
        assert result["severity"] == SEVERITY_NEVER_COMPLETED
        assert result["days_delta"] == 0

    def test_due_today(self):
        """An item exactly at now() is due (0 days overdue)."""
        item = _make_item(next_due_at=_NOW)
        result = classify_item(item, now=_NOW)
        assert result is not None
        assert result["severity"] == SEVERITY_DUE
        assert result["days_delta"] == 0

    def test_due_three_days_ago(self):
        """An item 3 days past due is classified as 'due'."""
        item = _make_item(next_due_at=_NOW - timedelta(days=3))
        result = classify_item(item, now=_NOW)
        assert result is not None
        assert result["severity"] == SEVERITY_DUE
        assert result["days_delta"] == -3

    def test_due_exactly_seven_days_ago(self):
        """An item exactly 7 days past due is still classified as 'due' (boundary)."""
        item = _make_item(next_due_at=_NOW - timedelta(days=7))
        result = classify_item(item, now=_NOW)
        assert result is not None
        assert result["severity"] == SEVERITY_DUE

    def test_overdue_eight_days_ago(self):
        """An item 8 days past due crosses into 'overdue' severity."""
        item = _make_item(next_due_at=_NOW - timedelta(days=8))
        result = classify_item(item, now=_NOW)
        assert result is not None
        assert result["severity"] == SEVERITY_OVERDUE
        assert result["days_delta"] == -8

    def test_overdue_exactly_thirty_days(self):
        """An item exactly 30 days past due is 'overdue' (boundary)."""
        item = _make_item(next_due_at=_NOW - timedelta(days=30))
        result = classify_item(item, now=_NOW)
        assert result is not None
        assert result["severity"] == SEVERITY_OVERDUE

    def test_critical_thirty_one_days(self):
        """An item 31+ days past due is 'critical'."""
        item = _make_item(next_due_at=_NOW - timedelta(days=31))
        result = classify_item(item, now=_NOW)
        assert result is not None
        assert result["severity"] == SEVERITY_CRITICAL

    def test_critical_sixty_days(self):
        """An item 60 days past due is also 'critical'."""
        item = _make_item(next_due_at=_NOW - timedelta(days=60))
        result = classify_item(item, now=_NOW)
        assert result is not None
        assert result["severity"] == SEVERITY_CRITICAL
        assert result["days_delta"] == -60

    def test_upcoming_tomorrow(self):
        """An item due tomorrow is 'upcoming'."""
        item = _make_item(next_due_at=_NOW + timedelta(days=1))
        result = classify_item(item, now=_NOW)
        assert result is not None
        assert result["severity"] == SEVERITY_UPCOMING

    def test_upcoming_exactly_seven_days(self):
        """An item due in exactly 7 days is 'upcoming' (boundary)."""
        item = _make_item(next_due_at=_NOW + timedelta(days=7))
        result = classify_item(item, now=_NOW)
        assert result is not None
        assert result["severity"] == SEVERITY_UPCOMING

    def test_not_due_beyond_lookahead(self):
        """An item due more than 7 days in the future is not classified (returns None)."""
        item = _make_item(next_due_at=_NOW + timedelta(days=8))
        result = classify_item(item, now=_NOW)
        assert result is None

    def test_not_due_far_future(self):
        """An item due 90 days from now returns None."""
        item = _make_item(next_due_at=_NOW + timedelta(days=90))
        result = classify_item(item, now=_NOW)
        assert result is None

    def test_completed_but_no_next_due_skipped(self):
        """An item with last_completed_at but no next_due_at returns None."""
        item = _make_item(
            last_completed_at=_NOW - timedelta(days=10),
            next_due_at=None,
        )
        result = classify_item(item, now=_NOW)
        assert result is None

    def test_result_preserves_name_and_category(self):
        """Classified item preserves name, category, and interval_days."""
        item = _make_item(
            name="HVAC Service",
            category="hvac",
            interval_days=365,
            next_due_at=_NOW - timedelta(days=5),
        )
        result = classify_item(item, now=_NOW)
        assert result is not None
        assert result["name"] == "HVAC Service"
        assert result["category"] == "hvac"
        assert result["interval_days"] == 365

    def test_naive_datetime_treated_as_utc(self):
        """Timezone-naive next_due_at is treated as UTC."""
        naive_dt = datetime(2026, 3, 20, 12, 0, 0)  # no tzinfo, 5 days before _NOW
        item = _make_item(next_due_at=naive_dt)
        result = classify_item(item, now=_NOW)
        assert result is not None
        assert result["severity"] == SEVERITY_DUE
        assert result["days_delta"] == -5


# ---------------------------------------------------------------------------
# build_notification_text tests
# ---------------------------------------------------------------------------


class TestBuildNotificationText:
    def _make_classified(self, severity: str, name: str, days_delta: int = 0):
        from butlers.jobs.home import ClassifiedItem

        return ClassifiedItem(
            id="test-id",
            name=name,
            category="filter",
            interval_days=90,
            severity=severity,
            days_delta=days_delta,
        )

    def test_empty_list_returns_empty_string(self):
        assert build_notification_text([]) == ""

    def test_single_critical_item(self):
        item = self._make_classified(SEVERITY_CRITICAL, "Water Heater", days_delta=-45)
        text = build_notification_text([item])
        assert "Home Maintenance Reminder" in text
        assert "CRITICAL" in text
        assert "Water Heater" in text
        assert "45 day(s) overdue" in text

    def test_single_due_item(self):
        item = self._make_classified(SEVERITY_DUE, "Air Filter", days_delta=-3)
        text = build_notification_text([item])
        assert "DUE" in text
        assert "Air Filter" in text
        assert "3 day(s) overdue" in text

    def test_single_upcoming_item(self):
        item = self._make_classified(SEVERITY_UPCOMING, "HVAC Service", days_delta=5)
        text = build_notification_text([item])
        assert "UPCOMING" in text
        assert "HVAC Service" in text
        assert "due in 5 day(s)" in text

    def test_single_never_completed_item(self):
        item = self._make_classified(SEVERITY_NEVER_COMPLETED, "Chimney Sweep", days_delta=0)
        text = build_notification_text([item])
        assert "NEVER COMPLETED" in text
        assert "Chimney Sweep" in text
        assert "never completed" in text

    def test_multiple_severities_ordered_correctly(self):
        """Critical appears before overdue, overdue before due, due before upcoming."""
        items = [
            self._make_classified(SEVERITY_UPCOMING, "Upcoming Task", days_delta=3),
            self._make_classified(SEVERITY_DUE, "Due Task", days_delta=-2),
            self._make_classified(SEVERITY_CRITICAL, "Critical Task", days_delta=-40),
            self._make_classified(SEVERITY_OVERDUE, "Overdue Task", days_delta=-15),
        ]
        text = build_notification_text(items)
        # Check ordering by position in the string
        critical_pos = text.index("CRITICAL")
        overdue_pos = text.index("OVERDUE")
        due_pos = text.index("DUE (")
        upcoming_pos = text.index("UPCOMING")
        assert critical_pos < overdue_pos < due_pos < upcoming_pos

    def test_category_included_in_output(self):
        item = self._make_classified(SEVERITY_DUE, "Dryer Filter", days_delta=-1)
        text = build_notification_text([item])
        assert "[filter]" in text

    def test_empty_groups_are_skipped(self):
        """Only groups with items are included in the output."""
        item = self._make_classified(SEVERITY_CRITICAL, "Critical Only", days_delta=-50)
        text = build_notification_text([item])
        assert "OVERDUE" not in text
        assert "UPCOMING" not in text
        assert "NEVER COMPLETED" not in text


# ---------------------------------------------------------------------------
# run_maintenance_schedule_check — no items
# ---------------------------------------------------------------------------


class TestMaintenanceCheckNoItems:
    async def test_no_items_returns_zero_counts(self):
        """When no items are due/upcoming, all counts are 0."""
        pool = _make_pool(fetch_rows=[])
        result = await run_maintenance_schedule_check(pool, None, _now=_NOW)
        assert result["items_checked"] == 0
        assert result["due_count"] == 0
        assert result["overdue_count"] == 0
        assert result["critical_count"] == 0
        assert result["upcoming_count"] == 0
        assert result["never_completed_count"] == 0
        assert result["reminders_sent"] == 0
        assert result["notification_text"] is None

    async def test_no_items_does_not_call_notify_fn(self):
        """When no items need attention, notify_fn is never called."""
        pool = _make_pool(fetch_rows=[])
        notify_fn = AsyncMock()
        await run_maintenance_schedule_check(pool, None, notify_fn=notify_fn, _now=_NOW)
        notify_fn.assert_not_called()


# ---------------------------------------------------------------------------
# run_maintenance_schedule_check — items present
# ---------------------------------------------------------------------------


class TestMaintenanceCheckWithItems:
    async def test_due_item_counted(self):
        """A single due item increments due_count."""
        row = _make_db_row(
            name="Air Filter",
            category="filter",
            next_due_at=_NOW - timedelta(days=3),
        )
        pool = _make_pool(fetch_rows=[row])
        result = await run_maintenance_schedule_check(pool, None, _now=_NOW)
        assert result["due_count"] == 1
        assert result["overdue_count"] == 0
        assert result["critical_count"] == 0

    async def test_overdue_item_counted(self):
        """A single overdue item (15 days) increments overdue_count."""
        row = _make_db_row(
            name="HVAC Filter",
            category="hvac",
            next_due_at=_NOW - timedelta(days=15),
        )
        pool = _make_pool(fetch_rows=[row])
        result = await run_maintenance_schedule_check(pool, None, _now=_NOW)
        assert result["overdue_count"] == 1
        assert result["due_count"] == 0

    async def test_critical_item_counted(self):
        """A single critical item (45 days) increments critical_count."""
        row = _make_db_row(
            name="Water Filter",
            category="filter",
            next_due_at=_NOW - timedelta(days=45),
        )
        pool = _make_pool(fetch_rows=[row])
        result = await run_maintenance_schedule_check(pool, None, _now=_NOW)
        assert result["critical_count"] == 1

    async def test_never_completed_item_counted(self):
        """An item with no completion date increments never_completed_count."""
        row = _make_db_row(
            name="Chimney Sweep",
            category="general",
            last_completed_at=None,
            next_due_at=None,
        )
        pool = _make_pool(fetch_rows=[row])
        result = await run_maintenance_schedule_check(pool, None, _now=_NOW)
        assert result["never_completed_count"] == 1

    async def test_upcoming_item_counted(self):
        """An upcoming item (3 days) increments upcoming_count."""
        row = _make_db_row(
            name="Service Visit",
            category="hvac",
            next_due_at=_NOW + timedelta(days=3),
        )
        pool = _make_pool(fetch_rows=[row])
        result = await run_maintenance_schedule_check(pool, None, _now=_NOW)
        assert result["upcoming_count"] == 1

    async def test_items_checked_reflects_db_rows(self):
        """items_checked equals the total number of rows returned from DB."""
        rows = [
            _make_db_row(name="Item A", next_due_at=_NOW - timedelta(days=3)),
            _make_db_row(name="Item B", next_due_at=_NOW - timedelta(days=20)),
            _make_db_row(name="Item C", next_due_at=_NOW + timedelta(days=4)),
        ]
        pool = _make_pool(fetch_rows=rows)
        result = await run_maintenance_schedule_check(pool, None, _now=_NOW)
        assert result["items_checked"] == 3

    async def test_notification_text_populated_when_items_present(self):
        """notification_text is set when items need attention."""
        row = _make_db_row(name="Air Filter", next_due_at=_NOW - timedelta(days=5))
        pool = _make_pool(fetch_rows=[row])
        result = await run_maintenance_schedule_check(pool, None, _now=_NOW)
        assert result["notification_text"] is not None
        assert "Air Filter" in result["notification_text"]

    async def test_notify_fn_called_when_items_present(self):
        """When notify_fn is provided and items are found, it is called once."""
        row = _make_db_row(name="Air Filter", next_due_at=_NOW - timedelta(days=5))
        pool = _make_pool(fetch_rows=[row])
        notify_fn = AsyncMock()
        result = await run_maintenance_schedule_check(pool, None, notify_fn=notify_fn, _now=_NOW)
        notify_fn.assert_awaited_once()
        assert result["reminders_sent"] == 1

    async def test_notify_fn_receives_notification_text(self):
        """The notify_fn is called with the formatted notification text."""
        row = _make_db_row(name="HVAC Filter", next_due_at=_NOW - timedelta(days=5))
        pool = _make_pool(fetch_rows=[row])
        received_messages: list[str] = []

        async def capture_notify(msg: str) -> None:
            received_messages.append(msg)

        await run_maintenance_schedule_check(pool, None, notify_fn=capture_notify, _now=_NOW)
        assert len(received_messages) == 1
        assert "HVAC Filter" in received_messages[0]

    async def test_no_notify_fn_does_not_send_reminder(self):
        """Without notify_fn, reminders_sent stays 0 even with due items."""
        row = _make_db_row(name="Air Filter", next_due_at=_NOW - timedelta(days=5))
        pool = _make_pool(fetch_rows=[row])
        result = await run_maintenance_schedule_check(pool, None, notify_fn=None, _now=_NOW)
        assert result["reminders_sent"] == 0
        # But notification_text is still populated
        assert result["notification_text"] is not None

    async def test_mixed_severities_counts(self):
        """Multiple items with different severities each counted in their bucket."""
        rows = [
            _make_db_row(name="Critical Item", next_due_at=_NOW - timedelta(days=45)),
            _make_db_row(name="Overdue Item", next_due_at=_NOW - timedelta(days=15)),
            _make_db_row(name="Due Item", next_due_at=_NOW - timedelta(days=3)),
            _make_db_row(name="Upcoming Item", next_due_at=_NOW + timedelta(days=4)),
            _make_db_row(
                name="Never Done",
                last_completed_at=None,
                next_due_at=None,
            ),
        ]
        pool = _make_pool(fetch_rows=rows)
        result = await run_maintenance_schedule_check(pool, None, _now=_NOW)
        assert result["critical_count"] == 1
        assert result["overdue_count"] == 1
        assert result["due_count"] == 1
        assert result["upcoming_count"] == 1
        assert result["never_completed_count"] == 1
        assert result["items_checked"] == 5


# ---------------------------------------------------------------------------
# run_maintenance_schedule_check — DB error propagation
# ---------------------------------------------------------------------------


class TestMaintenanceCheckDbErrors:
    async def test_db_query_failure_propagates(self):
        """If the DB query raises, the exception is re-raised."""
        pool = MagicMock()
        pool.fetch = AsyncMock(side_effect=Exception("connection refused"))

        with pytest.raises(Exception, match="connection refused"):
            await run_maintenance_schedule_check(pool, None, _now=_NOW)

    async def test_notify_fn_failure_does_not_crash_job(self):
        """If notify_fn raises, the exception is logged and reminders_sent stays 0."""
        row = _make_db_row(name="Air Filter", next_due_at=_NOW - timedelta(days=5))
        pool = _make_pool(fetch_rows=[row])

        async def broken_notify(msg: str) -> None:
            raise RuntimeError("Telegram unavailable")

        result = await run_maintenance_schedule_check(
            pool, None, notify_fn=broken_notify, _now=_NOW
        )
        # Job completes despite notify failure
        assert result["due_count"] == 1
        assert result["reminders_sent"] == 0  # not incremented on failure


# ---------------------------------------------------------------------------
# run_maintenance_schedule_check — job_args
# ---------------------------------------------------------------------------


class TestMaintenanceCheckJobArgs:
    async def test_job_args_accepted_and_ignored(self):
        """job_args is accepted without error (reserved for future use)."""
        pool = _make_pool(fetch_rows=[])
        result = await run_maintenance_schedule_check(pool, {"unused": "arg"}, _now=_NOW)
        assert result["items_checked"] == 0
