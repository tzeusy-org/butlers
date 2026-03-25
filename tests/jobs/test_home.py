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

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.jobs.home import (
    _DEFAULT_BATTERY_THRESHOLDS,
    _DEFAULT_OFFLINE_HOURS_THRESHOLDS,
    SEVERITY_CRITICAL,
    SEVERITY_DUE,
    SEVERITY_NEVER_COMPLETED,
    SEVERITY_OVERDUE,
    SEVERITY_UPCOMING,
    MaintenanceItemRow,
    _build_health_check_notification,
    build_notification_text,
    classify_battery,
    classify_item,
    classify_offline,
    run_device_health_check,
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
) -> dict:
    """Simulate an asyncpg record row from home.maintenance_items.

    Returns a plain dict; the production code only uses ``row["key"]`` access
    (same interface as asyncpg Record), avoiding MagicMock special-method brittleness.
    """
    return {
        "id": id,
        "name": name,
        "category": category,
        "interval_days": interval_days,
        "last_completed_at": last_completed_at,
        "next_due_at": next_due_at,
        "notes": notes,
    }


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


# ---------------------------------------------------------------------------
# Device health check helpers and main entry point
# ---------------------------------------------------------------------------


def _make_entity_row(
    entity_id: str = "sensor.living_room_temp",
    state: str = "22.5",
    attributes: dict | None = None,
    last_updated: datetime | None = None,
) -> MagicMock:
    """Return a mock asyncpg record row simulating ha_entity_snapshot."""
    if last_updated is None:
        last_updated = datetime.now(UTC) - timedelta(minutes=5)
    row = MagicMock()
    fn = entity_id.replace(".", " ").replace("_", " ")
    row.__getitem__ = lambda self, key: {
        "entity_id": entity_id,
        "state": state,
        "attributes": attributes or {"friendly_name": fn},
        "last_updated": last_updated,
    }[key]
    return row


def _make_health_pool(
    *,
    state_value: Any = None,
    entity_rows: list[Any] | None = None,
) -> MagicMock:
    """Return a minimal mock asyncpg pool for device health check tests."""
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=json.dumps(state_value) if state_value else None)
    pool.fetch = AsyncMock(return_value=entity_rows or [])
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    conn_mock = AsyncMock()
    conn_mock.fetchrow = AsyncMock(return_value=None)
    conn_mock.__aenter__ = AsyncMock(return_value=conn_mock)
    conn_mock.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=conn_mock)
    return pool


class TestClassifyBattery:
    """Battery severity classification against default thresholds."""

    thresholds = _DEFAULT_BATTERY_THRESHOLDS  # {"critical": 10, "warning": 20, "info": 30}

    def test_exactly_at_critical_threshold(self):
        assert classify_battery(10.0, self.thresholds) == "critical"

    def test_below_critical_threshold(self):
        assert classify_battery(5.0, self.thresholds) == "critical"

    def test_zero_is_critical(self):
        assert classify_battery(0.0, self.thresholds) == "critical"

    def test_one_above_critical_is_warning(self):
        # 11% → warning (11-20 range)
        assert classify_battery(11.0, self.thresholds) == "warning"

    def test_exactly_at_warning_threshold(self):
        assert classify_battery(20.0, self.thresholds) == "warning"

    def test_one_above_warning_is_info(self):
        # 21% → info (21-30 range)
        assert classify_battery(21.0, self.thresholds) == "info"

    def test_exactly_at_info_threshold(self):
        assert classify_battery(30.0, self.thresholds) == "info"

    def test_above_info_threshold_is_healthy(self):
        assert classify_battery(31.0, self.thresholds) is None

    def test_high_battery_is_healthy(self):
        assert classify_battery(100.0, self.thresholds) is None

    def test_custom_thresholds_critical(self):
        custom = {"critical": 15, "warning": 30, "info": 50}
        assert classify_battery(15.0, custom) == "critical"
        assert classify_battery(16.0, custom) == "warning"
        assert classify_battery(30.0, custom) == "warning"
        assert classify_battery(31.0, custom) == "info"
        assert classify_battery(50.0, custom) == "info"
        assert classify_battery(51.0, custom) is None

    def test_fractional_battery_levels(self):
        assert classify_battery(9.9, self.thresholds) == "critical"
        assert classify_battery(10.1, self.thresholds) == "warning"
        assert classify_battery(20.1, self.thresholds) == "info"
        assert classify_battery(30.1, self.thresholds) is None


class TestClassifyOffline:
    """Offline device classification against default thresholds."""

    thresholds = _DEFAULT_OFFLINE_HOURS_THRESHOLDS  # {"critical": 24, "warning": 1}

    def _ts(self, hours_ago: float) -> datetime:
        """Return a UTC datetime that is *hours_ago* hours in the past."""
        return datetime.now(UTC) - timedelta(hours=hours_ago)

    def test_none_last_changed_is_critical(self):
        assert classify_offline(None, self.thresholds) == "critical"

    def test_more_than_24h_is_critical(self):
        assert classify_offline(self._ts(25.0), self.thresholds) == "critical"

    def test_just_above_24h_is_critical(self):
        assert classify_offline(self._ts(25.0), self.thresholds) == "critical"

    def test_just_below_24h_is_warning(self):
        # 23h offline → warning (1h < 23h <= 24h)
        result = classify_offline(self._ts(23.0), self.thresholds)
        assert result == "warning"

    def test_between_1h_and_24h_is_warning(self):
        assert classify_offline(self._ts(2.0), self.thresholds) == "warning"
        assert classify_offline(self._ts(12.0), self.thresholds) == "warning"

    def test_just_above_1h_is_warning(self):
        assert classify_offline(self._ts(2.0), self.thresholds) == "warning"

    def test_less_than_1h_is_healthy(self):
        assert classify_offline(self._ts(0.5), self.thresholds) is None

    def test_recent_change_is_healthy(self):
        assert classify_offline(self._ts(0.0), self.thresholds) is None

    def test_naive_datetime_treated_as_utc(self):
        naive_ts = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=30)
        result = classify_offline(naive_ts, self.thresholds)
        assert result == "critical"

    def test_custom_thresholds(self):
        custom = {"critical": 6, "warning": 2}
        assert classify_offline(self._ts(7.0), custom) == "critical"
        assert classify_offline(self._ts(3.0), custom) == "warning"
        assert classify_offline(self._ts(0.5), custom) is None


class TestBuildHealthCheckNotification:
    def _issue(self, name: str, issue_type: str, severity: str, description: str) -> dict[str, Any]:
        return {
            "entity_id": f"sensor.{name}",
            "friendly_name": name.replace("_", " ").title(),
            "issue_type": issue_type,
            "severity": severity,
            "value": "unavailable" if issue_type == "offline" else 5.0,
            "description": description,
        }

    def test_all_clear_no_issues(self):
        msg = _build_health_check_notification(
            issues=[],
            devices_checked=10,
            critical_count=0,
            warning_count=0,
            info_count=0,
        )
        assert "\u2705" in msg
        assert "10" in msg
        assert "no issues" in msg.lower()

    def test_all_clear_with_info_only(self):
        info_issue = self._issue("hallway_sensor_battery", "battery", "info", "battery at 28%")
        msg = _build_health_check_notification(
            issues=[info_issue],
            devices_checked=10,
            critical_count=0,
            warning_count=0,
            info_count=1,
        )
        assert "\u2705" in msg
        assert "battery at 28%" in msg

    def test_critical_issues_listed_first(self):
        critical = self._issue(
            "basement_sensor", "offline", "critical", "offline (state: unavailable)"
        )
        warning = self._issue("garage_battery", "battery", "warning", "battery at 15%")
        msg = _build_health_check_notification(
            issues=[warning, critical],  # deliberately out of order
            devices_checked=5,
            critical_count=1,
            warning_count=1,
            info_count=0,
        )
        assert "\U0001f534" in msg
        assert "\U0001f7e0" in msg
        assert msg.index("\U0001f534") < msg.index("\U0001f7e0"), (
            "Critical section must precede warning section"
        )
        assert "offline (state: unavailable)" in msg
        assert "battery at 15%" in msg

    def test_critical_only(self):
        critical = self._issue("smoke_detector", "offline", "critical", "offline (state: unknown)")
        msg = _build_health_check_notification(
            issues=[critical],
            devices_checked=3,
            critical_count=1,
            warning_count=0,
            info_count=0,
        )
        assert "\U0001f534" in msg
        assert "\U0001f7e0" not in msg
        assert "offline (state: unknown)" in msg

    def test_warning_only(self):
        warning = self._issue("kitchen_sensor_battery", "battery", "warning", "battery at 18%")
        msg = _build_health_check_notification(
            issues=[warning],
            devices_checked=8,
            critical_count=0,
            warning_count=1,
            info_count=0,
        )
        assert "\U0001f7e0" in msg
        assert "\U0001f534" not in msg

    def test_device_count_in_notification(self):
        msg = _build_health_check_notification(
            issues=[],
            devices_checked=42,
            critical_count=0,
            warning_count=0,
            info_count=0,
        )
        assert "42" in msg


class TestRunDeviceHealthCheck:
    """Tests for run_device_health_check job handler with mocked dependencies."""

    def _pool_with_rows(self, rows: list[Any], state_value: Any = None) -> MagicMock:
        return _make_health_pool(state_value=state_value, entity_rows=rows)

    async def test_empty_snapshot_returns_error(self):
        pool = self._pool_with_rows([])
        with patch(
            "butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock
        ) as mock_notify:
            result = await run_device_health_check(pool, None)

        assert result == {"error": "no_entity_snapshot"}
        mock_notify.assert_called_once()
        assert (
            "unavailable" in mock_notify.call_args[0][1].lower()
            or "entity data" in mock_notify.call_args[0][1].lower()
        )

    async def test_all_healthy_devices(self):
        rows = [
            _make_entity_row("sensor.living_room_temp", state="22.5"),
            _make_entity_row("light.kitchen", state="off"),
        ]
        pool = self._pool_with_rows(rows)
        with (
            patch(
                "butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock
            ) as mock_notify,
            patch("butlers.jobs.home._store_device_fact", new_callable=AsyncMock),
        ):
            result = await run_device_health_check(pool, None)

        assert result["devices_checked"] == 2
        assert result["issues_found"] == 0
        assert result["critical_count"] == 0
        assert result["warning_count"] == 0
        mock_notify.assert_called_once()
        assert "\u2705" in mock_notify.call_args[0][1]

    async def test_critical_battery_issue(self):
        rows = [
            _make_entity_row(
                "sensor.basement_battery",
                state="8",
                attributes={"friendly_name": "Basement Battery"},
            ),
        ]
        pool = self._pool_with_rows(rows)
        with (
            patch(
                "butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock
            ) as mock_notify,
            patch("butlers.jobs.home._store_device_fact", new_callable=AsyncMock) as mock_store,
        ):
            result = await run_device_health_check(pool, None)

        assert result["devices_checked"] == 1
        assert result["issues_found"] == 1
        assert result["critical_count"] == 1
        assert result["warning_count"] == 0

        msg = mock_notify.call_args[0][1]
        assert "\U0001f534" in msg
        assert "8" in msg

        mock_store.assert_called_once()
        call_kwargs = mock_store.call_args[1]
        assert call_kwargs["importance"] == 8.0
        assert "battery" in call_kwargs["tags"]
        assert "maintenance" in call_kwargs["tags"]

    async def test_offline_warning_issue(self):
        last_updated = datetime.now(UTC) - timedelta(hours=3)
        rows = [
            _make_entity_row(
                "binary_sensor.front_door",
                state="unavailable",
                attributes={"friendly_name": "Front Door"},
                last_updated=last_updated,
            ),
        ]
        pool = self._pool_with_rows(rows)
        with (
            patch(
                "butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock
            ) as mock_notify,
            patch("butlers.jobs.home._store_device_fact", new_callable=AsyncMock) as mock_store,
        ):
            result = await run_device_health_check(pool, None)

        assert result["issues_found"] == 1
        assert result["warning_count"] == 1
        assert result["critical_count"] == 0

        msg = mock_notify.call_args[0][1]
        assert "\U0001f7e0" in msg

        call_kwargs = mock_store.call_args[1]
        assert call_kwargs["importance"] == 6.5
        assert "offline" in call_kwargs["tags"]

    async def test_mixed_issues(self):
        last_updated_critical = datetime.now(UTC) - timedelta(hours=30)
        rows = [
            _make_entity_row(
                "sensor.basement_battery",
                state="5",
                attributes={"friendly_name": "Basement Battery"},
            ),
            _make_entity_row(
                "sensor.garage_motion",
                state="unavailable",
                attributes={"friendly_name": "Garage Motion"},
                last_updated=last_updated_critical,
            ),
        ]
        pool = self._pool_with_rows(rows)
        with (
            patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
            patch("butlers.jobs.home._store_device_fact", new_callable=AsyncMock) as mock_store,
        ):
            result = await run_device_health_check(pool, None)

        assert result["issues_found"] == 2
        assert result["critical_count"] == 2
        assert mock_store.call_count == 2

    async def test_no_notification_when_notify_skipped(self):
        """Notification failure (no bot token) is non-fatal."""
        rows = [_make_entity_row("sensor.temp", state="22")]
        pool = self._pool_with_rows(rows)
        with (
            patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
            patch("butlers.jobs.home._store_device_fact", new_callable=AsyncMock),
        ):
            result = await run_device_health_check(pool, None)
        assert "devices_checked" in result

    async def test_battery_at_info_threshold_creates_info_issue(self):
        rows = [
            _make_entity_row(
                "sensor.hallway_battery",
                state="28",
                attributes={"friendly_name": "Hallway Battery"},
            ),
        ]
        pool = self._pool_with_rows(rows)
        with (
            patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
            patch("butlers.jobs.home._store_device_fact", new_callable=AsyncMock) as mock_store,
        ):
            result = await run_device_health_check(pool, None)

        assert result["issues_found"] == 1
        call_kwargs = mock_store.call_args[1]
        assert call_kwargs["importance"] == 5.0

    async def test_non_battery_entity_not_classified_as_battery(self):
        """Entities without 'battery' in name/id are not battery-checked."""
        rows = [
            _make_entity_row("sensor.living_room_temp", state="5"),
        ]
        pool = self._pool_with_rows(rows)
        with (
            patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
            patch("butlers.jobs.home._store_device_fact", new_callable=AsyncMock),
        ):
            result = await run_device_health_check(pool, None)

        assert result["issues_found"] == 0

    async def test_job_args_ignored(self):
        """Passing job_args does not raise."""
        rows = [_make_entity_row("sensor.temp", state="25")]
        pool = self._pool_with_rows(rows)
        with (
            patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
            patch("butlers.jobs.home._store_device_fact", new_callable=AsyncMock),
        ):
            result = await run_device_health_check(pool, {"some_arg": "value"})
        assert "devices_checked" in result

    async def test_unknown_state_classified_as_offline(self):
        """Entities with state='unknown' are treated as offline."""
        last_updated = datetime.now(UTC) - timedelta(hours=50)
        rows = [
            _make_entity_row(
                "sensor.water_leak",
                state="unknown",
                attributes={"friendly_name": "Water Leak Sensor"},
                last_updated=last_updated,
            ),
        ]
        pool = self._pool_with_rows(rows)
        with (
            patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
            patch("butlers.jobs.home._store_device_fact", new_callable=AsyncMock),
        ):
            result = await run_device_health_check(pool, None)

        assert result["issues_found"] == 1
        assert result["critical_count"] == 1

    async def test_string_attributes_decoded_from_json(self):
        """Attributes stored as JSON strings are decoded correctly."""
        row = MagicMock()
        row.__getitem__ = lambda self, key: {
            "entity_id": "sensor.kitchen_battery",
            "state": "8",
            "attributes": '{"friendly_name": "Kitchen Battery"}',
            "last_updated": datetime.now(UTC),
        }[key]
        pool = self._pool_with_rows([row])
        with (
            patch("butlers.jobs.home._notify_owner_telegram", new_callable=AsyncMock),
            patch("butlers.jobs.home._store_device_fact", new_callable=AsyncMock),
        ):
            result = await run_device_health_check(pool, None)

        assert result["critical_count"] == 1


class TestDaemonRegistration:
    def test_device_health_check_registered_for_home(self):
        """device_health_check must appear in the registry for the home butler."""
        from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        home_jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("home", {})
        assert "device_health_check" in home_jobs, (
            "device_health_check not registered in _DETERMINISTIC_SCHEDULE_JOB_REGISTRY['home']"
        )

    def test_device_health_check_handler_is_callable(self):
        """The home device_health_check handler must be callable."""
        from butlers.daemon import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

        handler = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("home", {}).get("device_health_check")
        assert callable(handler), "device_health_check handler is not callable"
