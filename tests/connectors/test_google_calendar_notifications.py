"""Tests for Google Calendar starting-soon notification logic.

Covers tasks 4.1-4.5 from the connector-google-calendar openspec:
- Task 4.1: Upcoming event window scan after each sync cycle
- Task 4.2: In-memory seen-set with (event_id, lead_minutes) dedup
- Task 4.3: event_starting_soon envelope with interactive policy tier
- Task 4.4: Seen-set pruning for past events
- Task 4.5: Restart recovery for overdue notifications
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.google_calendar import (
    GoogleCalendarConnectorConfig,
    StartingSoonSeenSet,
    _parse_event_start,
    build_event_envelope,
    build_starting_soon_envelope,
    scan_starting_soon,
    scan_starting_soon_on_restart,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    event_id: str,
    *,
    minutes_from_now: int,
    status: str = "confirmed",
    title: str = "Test Event",
    organizer_email: str = "organizer@example.com",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create a minimal Google Calendar event dict for testing."""
    if now is None:
        now = datetime.now(UTC)
    start_time = now + timedelta(minutes=minutes_from_now)
    return {
        "id": event_id,
        "summary": title,
        "status": status,
        "start": {"dateTime": start_time.isoformat()},
        "end": {"dateTime": (start_time + timedelta(hours=1)).isoformat()},
        "organizer": {"email": organizer_email},
        "updated": now.isoformat(),
    }


def _make_all_day_event(
    event_id: str,
    *,
    days_from_now: int,
    status: str = "confirmed",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create a minimal all-day Google Calendar event dict for testing."""
    if now is None:
        now = datetime.now(UTC)
    target_date = (now + timedelta(days=days_from_now)).strftime("%Y-%m-%d")
    return {
        "id": event_id,
        "summary": "All Day Event",
        "status": status,
        "start": {"date": target_date},
        "end": {"date": target_date},
        "organizer": {"email": "organizer@example.com"},
        "updated": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# StartingSoonSeenSet tests (Task 4.2, 4.4)
# ---------------------------------------------------------------------------


class TestStartingSoonSeenSet:
    """Tests for StartingSoonSeenSet dedup and pruning."""

    def test_new_set_is_empty(self) -> None:
        """A fresh seen-set has no entries."""
        seen = StartingSoonSeenSet()
        assert not seen.has_seen("evt-1", 15)
        assert seen.size() == 0

    def test_mark_seen_adds_entry(self) -> None:
        """mark_seen records an event_id+lead_minutes pair."""
        seen = StartingSoonSeenSet()
        start = datetime.now(UTC) + timedelta(minutes=10)
        seen.mark_seen("evt-1", 15, start)
        assert seen.has_seen("evt-1", 15)
        assert seen.size() == 1

    def test_different_lead_minutes_are_distinct(self) -> None:
        """Same event_id with different lead_minutes counts as different entries."""
        seen = StartingSoonSeenSet()
        start = datetime.now(UTC) + timedelta(minutes=10)
        seen.mark_seen("evt-1", 15, start)
        assert seen.has_seen("evt-1", 15)
        assert not seen.has_seen("evt-1", 30)

    def test_different_event_ids_are_distinct(self) -> None:
        """Same lead_minutes with different event_ids counts as different entries."""
        seen = StartingSoonSeenSet()
        start = datetime.now(UTC) + timedelta(minutes=10)
        seen.mark_seen("evt-1", 15, start)
        assert seen.has_seen("evt-1", 15)
        assert not seen.has_seen("evt-2", 15)

    def test_prune_removes_past_events(self) -> None:
        """Task 4.4: Prune removes entries whose event_start is in the past."""
        seen = StartingSoonSeenSet()
        now = datetime.now(UTC)

        # Past event (started 90 min ago - beyond SEEN_SET_PRUNE_PAST_MINUTES=60)
        past_start = now - timedelta(minutes=90)
        seen.mark_seen("past-evt", 15, past_start)

        # Future event
        future_start = now + timedelta(minutes=10)
        seen.mark_seen("future-evt", 15, future_start)

        pruned = seen.prune_past_events(now=now)
        assert pruned == 1
        assert not seen.has_seen("past-evt", 15)
        assert seen.has_seen("future-evt", 15)
        assert seen.size() == 1

    def test_prune_keeps_recently_started_events(self) -> None:
        """Events started within PRUNE_PAST_MINUTES (60) are kept."""
        seen = StartingSoonSeenSet()
        now = datetime.now(UTC)

        # Started 30 min ago (within the 60-min prune window)
        recent_start = now - timedelta(minutes=30)
        seen.mark_seen("recent-evt", 15, recent_start)

        pruned = seen.prune_past_events(now=now)
        assert pruned == 0
        assert seen.has_seen("recent-evt", 15)

    def test_prune_empty_set_is_safe(self) -> None:
        """Pruning an empty seen-set does nothing."""
        seen = StartingSoonSeenSet()
        pruned = seen.prune_past_events()
        assert pruned == 0
        assert seen.size() == 0

    def test_prune_all_entries(self) -> None:
        """All entries can be pruned when all events are past."""
        seen = StartingSoonSeenSet()
        now = datetime.now(UTC)
        old = now - timedelta(hours=3)
        seen.mark_seen("evt-1", 15, old)
        seen.mark_seen("evt-2", 30, old)
        seen.mark_seen("evt-3", 15, old)
        pruned = seen.prune_past_events(now=now)
        assert pruned == 3
        assert seen.size() == 0


# ---------------------------------------------------------------------------
# build_starting_soon_envelope tests (Task 4.3)
# ---------------------------------------------------------------------------


class TestBuildStartingSoonEnvelope:
    """Tests for the starting-soon ingest.v1 envelope builder."""

    def test_envelope_schema_version(self) -> None:
        """Envelope has correct schema_version."""
        event = _make_event("evt-1", minutes_from_now=10)
        env = build_starting_soon_envelope(
            event,
            lead_minutes=15,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert env["schema_version"] == "ingest.v1"

    def test_envelope_policy_tier_is_interactive(self) -> None:
        """Task 4.3: starting-soon envelope has interactive policy_tier."""
        event = _make_event("evt-1", minutes_from_now=10)
        env = build_starting_soon_envelope(
            event,
            lead_minutes=15,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert env["control"]["policy_tier"] == "interactive"

    def test_envelope_external_event_id_prefix(self) -> None:
        """external_event_id is prefixed with 'starting_soon:'."""
        event = _make_event("evt-1", minutes_from_now=10)
        env = build_starting_soon_envelope(
            event,
            lead_minutes=15,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert env["event"]["external_event_id"] == "starting_soon:evt-1"

    def test_envelope_idempotency_key_includes_lead_minutes(self) -> None:
        """idempotency_key encodes the lead_minutes for dedup."""
        event = _make_event("evt-1", minutes_from_now=10)
        env = build_starting_soon_envelope(
            event,
            lead_minutes=15,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        key = env["control"]["idempotency_key"]
        assert "starting_soon" in key
        assert "evt-1" in key
        assert "15" in key

    def test_envelope_source_fields(self) -> None:
        """Envelope source fields match configuration."""
        event = _make_event("evt-1", minutes_from_now=10)
        env = build_starting_soon_envelope(
            event,
            lead_minutes=15,
            endpoint_identity="google_calendar:user:alice@gmail.com",
            connector_channel="google_calendar",
            connector_provider="google_calendar",
        )
        assert env["source"]["channel"] == "google_calendar"
        assert env["source"]["provider"] == "google_calendar"
        assert env["source"]["endpoint_identity"] == "google_calendar:user:alice@gmail.com"

    def test_envelope_normalized_text_contains_starting_soon(self) -> None:
        """Normalized text indicates starting_soon event type."""
        event = _make_event("evt-1", minutes_from_now=10, title="Team Standup")
        env = build_starting_soon_envelope(
            event,
            lead_minutes=15,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert "starting_soon" in env["payload"]["normalized_text"]
        assert "Team Standup" in env["payload"]["normalized_text"]

    def test_envelope_different_lead_minutes_differ(self) -> None:
        """Two envelopes with different lead_minutes have different idempotency keys."""
        event = _make_event("evt-1", minutes_from_now=10)
        env15 = build_starting_soon_envelope(
            event,
            lead_minutes=15,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        env30 = build_starting_soon_envelope(
            event,
            lead_minutes=30,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert env15["control"]["idempotency_key"] != env30["control"]["idempotency_key"]


# ---------------------------------------------------------------------------
# build_event_envelope tests
# ---------------------------------------------------------------------------


class TestBuildEventEnvelope:
    """Tests for the standard event change ingest.v1 envelope builder."""

    def test_envelope_policy_tier_is_default(self) -> None:
        """Standard event changes have default policy_tier."""
        event = _make_event("evt-1", minutes_from_now=60)
        env = build_event_envelope(
            event,
            event_type="event_created",
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert env["control"]["policy_tier"] == "default"

    def test_envelope_ingestion_tier_is_full(self) -> None:
        """Standard event changes have full ingestion_tier."""
        event = _make_event("evt-1", minutes_from_now=60)
        env = build_event_envelope(
            event,
            event_type="event_created",
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert env["control"]["ingestion_tier"] == "full"

    def test_envelope_external_event_id(self) -> None:
        """external_event_id matches the Google Calendar event id."""
        event = _make_event("evt-abc123", minutes_from_now=60)
        env = build_event_envelope(
            event,
            event_type="event_updated",
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert env["event"]["external_event_id"] == "evt-abc123"
        assert env["event"]["external_thread_id"] == "evt-abc123"

    def test_normalized_text_format(self) -> None:
        """normalized_text contains event_type and title."""
        event = _make_event("evt-1", minutes_from_now=60, title="Quarterly Review")
        env = build_event_envelope(
            event,
            event_type="event_created",
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        text = env["payload"]["normalized_text"]
        assert "[Calendar: event_created]" in text
        assert "Quarterly Review" in text


# ---------------------------------------------------------------------------
# scan_starting_soon tests (Tasks 4.1, 4.2, 4.3, 4.4)
# ---------------------------------------------------------------------------


class TestScanStartingSoon:
    """Tests for the scan_starting_soon function."""

    def test_no_notifications_when_lead_minutes_zero(self) -> None:
        """Task config: lead_minutes=0 disables starting-soon notifications."""
        now = datetime.now(UTC)
        events = [_make_event("evt-1", minutes_from_now=5, now=now)]
        seen = StartingSoonSeenSet()
        result = scan_starting_soon(
            events,
            seen,
            lead_minutes=0,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert result == []

    def test_event_in_window_triggers_notification(self) -> None:
        """Task 4.1: Event starting within lead-time window triggers notification."""
        now = datetime.now(UTC)
        # Event starts in 10 minutes; lead_minutes=15 → within window
        events = [_make_event("evt-1", minutes_from_now=10, now=now)]
        seen = StartingSoonSeenSet()
        result = scan_starting_soon(
            events,
            seen,
            lead_minutes=15,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert len(result) == 1
        assert result[0]["event"]["external_event_id"] == "starting_soon:evt-1"

    def test_event_outside_window_does_not_trigger(self) -> None:
        """Events outside the lead-time window do not trigger notifications."""
        now = datetime.now(UTC)
        # Event starts in 30 minutes; lead_minutes=15 → outside window
        events = [_make_event("evt-1", minutes_from_now=30, now=now)]
        seen = StartingSoonSeenSet()
        result = scan_starting_soon(
            events,
            seen,
            lead_minutes=15,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert result == []

    def test_past_event_does_not_trigger(self) -> None:
        """Events that have already started do not trigger notifications."""
        now = datetime.now(UTC)
        # Event started 5 minutes ago
        events = [_make_event("evt-1", minutes_from_now=-5, now=now)]
        seen = StartingSoonSeenSet()
        result = scan_starting_soon(
            events,
            seen,
            lead_minutes=15,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert result == []

    def test_dedup_prevents_duplicate_notifications(self) -> None:
        """Task 4.2: Already-seen events are not notified again."""
        now = datetime.now(UTC)
        events = [_make_event("evt-1", minutes_from_now=10, now=now)]
        seen = StartingSoonSeenSet()

        # First scan: should trigger
        result1 = scan_starting_soon(
            events,
            seen,
            lead_minutes=15,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert len(result1) == 1

        # Second scan with same event in window: should NOT trigger again
        result2 = scan_starting_soon(
            events,
            seen,
            lead_minutes=15,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert result2 == []

    def test_cancelled_events_are_skipped(self) -> None:
        """Cancelled events do not trigger starting-soon notifications."""
        now = datetime.now(UTC)
        events = [_make_event("evt-1", minutes_from_now=10, now=now, status="cancelled")]
        seen = StartingSoonSeenSet()
        result = scan_starting_soon(
            events,
            seen,
            lead_minutes=15,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert result == []

    def test_multiple_events_some_in_window(self) -> None:
        """Only events in the lead-time window are notified."""
        now = datetime.now(UTC)
        events = [
            _make_event("evt-1", minutes_from_now=5, now=now),  # in window (15 min)
            _make_event("evt-2", minutes_from_now=20, now=now),  # outside window
            _make_event("evt-3", minutes_from_now=12, now=now),  # in window
        ]
        seen = StartingSoonSeenSet()
        result = scan_starting_soon(
            events,
            seen,
            lead_minutes=15,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        notified_ids = {r["event"]["external_event_id"] for r in result}
        assert "starting_soon:evt-1" in notified_ids
        assert "starting_soon:evt-3" in notified_ids
        assert "starting_soon:evt-2" not in notified_ids

    def test_scan_marks_events_seen(self) -> None:
        """Task 4.2: scan_starting_soon marks notified events in seen-set."""
        now = datetime.now(UTC)
        events = [_make_event("evt-1", minutes_from_now=10, now=now)]
        seen = StartingSoonSeenSet()
        scan_starting_soon(
            events,
            seen,
            lead_minutes=15,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert seen.has_seen("evt-1", 15)

    def test_prune_is_called_before_scan(self) -> None:
        """Task 4.4: Old seen-set entries are pruned before scanning."""
        now = datetime.now(UTC)

        # Add an old seen entry for a different event
        seen = StartingSoonSeenSet()
        old_start = now - timedelta(hours=2)  # Well past the prune threshold
        seen.mark_seen("old-evt", 15, old_start)
        assert seen.size() == 1

        events = [_make_event("evt-new", minutes_from_now=10, now=now)]
        result = scan_starting_soon(
            events,
            seen,
            lead_minutes=15,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        # Old entry should be pruned, new event should be notified
        assert not seen.has_seen("old-evt", 15)
        assert seen.has_seen("evt-new", 15)
        assert len(result) == 1

    def test_notification_envelope_has_interactive_tier(self) -> None:
        """Task 4.3: Notifications have interactive policy_tier."""
        now = datetime.now(UTC)
        events = [_make_event("evt-1", minutes_from_now=10, now=now)]
        seen = StartingSoonSeenSet()
        result = scan_starting_soon(
            events,
            seen,
            lead_minutes=15,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert len(result) == 1
        assert result[0]["control"]["policy_tier"] == "interactive"

    def test_all_day_event_in_future_triggers(self) -> None:
        """All-day events (date-only) in the future can also be scanned."""
        now = datetime.now(UTC)
        # All-day event tomorrow — far outside any 15-min window, but test the parser
        event = _make_all_day_event("evt-allday", days_from_now=1, now=now)
        seen = StartingSoonSeenSet()
        # Should NOT trigger since it's 1+ day away
        result = scan_starting_soon(
            [event],
            seen,
            lead_minutes=15,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert result == []

    def test_empty_events_list(self) -> None:
        """Scanning an empty events list returns no notifications."""
        now = datetime.now(UTC)
        seen = StartingSoonSeenSet()
        result = scan_starting_soon(
            [],
            seen,
            lead_minutes=15,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert result == []


# ---------------------------------------------------------------------------
# scan_starting_soon_on_restart tests (Task 4.5)
# ---------------------------------------------------------------------------


class TestScanStartingSoonOnRestart:
    """Tests for restart recovery — task 4.5."""

    def test_restart_emits_overdue_notification(self) -> None:
        """Task 4.5: Restart emits notification for events in window that haven't started yet."""
        now = datetime.now(UTC)
        # Event starts in 5 minutes — it's within the 15-min lead window
        events = [_make_event("evt-1", minutes_from_now=5, now=now)]
        seen = StartingSoonSeenSet()
        result = scan_starting_soon_on_restart(
            events,
            seen,
            lead_minutes=15,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert len(result) == 1
        assert result[0]["event"]["external_event_id"] == "starting_soon:evt-1"

    def test_restart_does_not_emit_for_already_started_events(self) -> None:
        """Task 4.5: Events that have already started are not notified on restart."""
        now = datetime.now(UTC)
        # Event started 2 minutes ago
        events = [_make_event("evt-1", minutes_from_now=-2, now=now)]
        seen = StartingSoonSeenSet()
        result = scan_starting_soon_on_restart(
            events,
            seen,
            lead_minutes=15,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert result == []

    def test_restart_does_not_emit_for_far_future_events(self) -> None:
        """Events far in the future (beyond lead window) are not notified on restart."""
        now = datetime.now(UTC)
        # Event starts in 60 minutes; lead_minutes=15 → outside window
        events = [_make_event("evt-1", minutes_from_now=60, now=now)]
        seen = StartingSoonSeenSet()
        result = scan_starting_soon_on_restart(
            events,
            seen,
            lead_minutes=15,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert result == []

    def test_restart_dedup_from_seen_set(self) -> None:
        """If seen-set already has entry (e.g. from concurrent restart), dedup applies."""
        now = datetime.now(UTC)
        events = [_make_event("evt-1", minutes_from_now=5, now=now)]
        seen = StartingSoonSeenSet()
        # Pre-populate seen-set (simulates concurrent restart scenario)
        seen.mark_seen("evt-1", 15, now + timedelta(minutes=5))

        result = scan_starting_soon_on_restart(
            events,
            seen,
            lead_minutes=15,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert result == []

    def test_restart_disabled_when_lead_minutes_zero(self) -> None:
        """lead_minutes=0 disables restart recovery notifications too."""
        now = datetime.now(UTC)
        events = [_make_event("evt-1", minutes_from_now=5, now=now)]
        seen = StartingSoonSeenSet()
        result = scan_starting_soon_on_restart(
            events,
            seen,
            lead_minutes=0,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert result == []

    def test_restart_marks_events_seen_after_notification(self) -> None:
        """Notified events are marked in the seen-set to prevent future duplicates."""
        now = datetime.now(UTC)
        events = [_make_event("evt-1", minutes_from_now=8, now=now)]
        seen = StartingSoonSeenSet()
        result = scan_starting_soon_on_restart(
            events,
            seen,
            lead_minutes=15,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert len(result) == 1
        assert seen.has_seen("evt-1", 15)

    def test_restart_recovery_envelope_has_interactive_tier(self) -> None:
        """Task 4.5 envelopes also use interactive policy_tier."""
        now = datetime.now(UTC)
        events = [_make_event("evt-1", minutes_from_now=5, now=now)]
        seen = StartingSoonSeenSet()
        result = scan_starting_soon_on_restart(
            events,
            seen,
            lead_minutes=15,
            now=now,
            endpoint_identity="google_calendar:user:alice@gmail.com",
        )
        assert len(result) == 1
        assert result[0]["control"]["policy_tier"] == "interactive"


# ---------------------------------------------------------------------------
# _parse_event_start tests
# ---------------------------------------------------------------------------


class TestParseEventStart:
    """Tests for event start time parsing helper."""

    def test_parse_datetime_with_timezone(self) -> None:
        """RFC3339 datetime with timezone is parsed correctly."""
        event = {"start": {"dateTime": "2026-04-01T10:00:00+00:00"}}
        dt = _parse_event_start(event)
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.year == 2026
        assert dt.month == 4
        assert dt.day == 1
        assert dt.hour == 10

    def test_parse_datetime_without_timezone_assumed_utc(self) -> None:
        """Naive datetime is assumed UTC."""
        event = {"start": {"dateTime": "2026-04-01T10:00:00"}}
        dt = _parse_event_start(event)
        assert dt is not None
        assert dt.tzinfo is not None

    def test_parse_datetime_with_z_suffix(self) -> None:
        """RFC3339 Z suffix (UTC) is parsed correctly — guards against fromisoformat rejects."""
        event = {"start": {"dateTime": "2026-04-01T10:00:00Z"}}
        dt = _parse_event_start(event)
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.year == 2026
        assert dt.month == 4
        assert dt.day == 1
        assert dt.hour == 10

    def test_parse_datetime_with_z_suffix_and_fractional_seconds(self) -> None:
        """RFC3339 Z suffix with fractional seconds (e.g. Google Calendar API) parses correctly."""
        event = {"start": {"dateTime": "2026-04-01T10:00:00.000Z"}}
        dt = _parse_event_start(event)
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.year == 2026
        assert dt.month == 4
        assert dt.day == 1
        assert dt.hour == 10

    def test_parse_all_day_event(self) -> None:
        """All-day events (YYYY-MM-DD) are parsed."""
        event = {"start": {"date": "2026-04-01"}}
        dt = _parse_event_start(event)
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 4
        assert dt.day == 1

    def test_parse_empty_start_returns_none(self) -> None:
        """Events with empty start field return None."""
        event = {"start": {}}
        dt = _parse_event_start(event)
        assert dt is None

    def test_parse_missing_start_returns_none(self) -> None:
        """Events without start field return None."""
        event = {}
        dt = _parse_event_start(event)
        assert dt is None


# ---------------------------------------------------------------------------
# Integration-style test: account runtime emit_starting_soon
# ---------------------------------------------------------------------------


class TestAccountRuntimeStartingSoon:
    """Integration test for GoogleCalendarAccountRuntime starting-soon emission."""

    def _make_config(
        self,
        lead_minutes: int = 15,
        endpoint_identity: str = "google_calendar:user:alice@gmail.com",
    ) -> GoogleCalendarConnectorConfig:
        return GoogleCalendarConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            connector_endpoint_identity=endpoint_identity,
            gcal_client_id="test-client-id",
            gcal_client_secret="test-client-secret",
            gcal_refresh_token="test-refresh-token",
            gcal_starting_soon_lead_minutes=lead_minutes,
        )

    async def test_emit_starting_soon_submits_envelope(self) -> None:
        """_emit_starting_soon_notifications submits envelopes for events in window."""
        from butlers.connectors.google_calendar import GoogleCalendarAccountRuntime

        config = self._make_config(lead_minutes=15)
        mock_mcp = MagicMock()
        mock_mcp.call_tool = AsyncMock(return_value={"status": "accepted", "request_id": "r-1"})

        runtime = GoogleCalendarAccountRuntime(config, mcp_client=mock_mcp)

        # Inject an upcoming event into the cache
        now = datetime.now(UTC)
        event = _make_event("evt-1", minutes_from_now=10, now=now)
        runtime._upcoming_events["evt-1"] = event

        await runtime._emit_starting_soon_notifications(is_restart=False)

        # Should have called ingest with a starting-soon envelope
        mock_mcp.call_tool.assert_called_once()
        call_args = mock_mcp.call_tool.call_args
        tool_name = call_args[0][0]
        envelope = call_args[0][1]
        assert tool_name == "ingest"
        assert envelope["control"]["policy_tier"] == "interactive"
        assert "starting_soon:evt-1" in envelope["event"]["external_event_id"]

    async def test_emit_no_notifications_when_disabled(self) -> None:
        """No notifications emitted when lead_minutes=0."""
        from butlers.connectors.google_calendar import GoogleCalendarAccountRuntime

        config = self._make_config(lead_minutes=0)
        mock_mcp = MagicMock()
        mock_mcp.call_tool = AsyncMock()

        runtime = GoogleCalendarAccountRuntime(config, mcp_client=mock_mcp)

        now = datetime.now(UTC)
        event = _make_event("evt-1", minutes_from_now=5, now=now)
        runtime._upcoming_events["evt-1"] = event

        await runtime._emit_starting_soon_notifications(is_restart=False)

        mock_mcp.call_tool.assert_not_called()

    async def test_restart_recovery_emits_overdue_notification(self) -> None:
        """Task 4.5: restart recovery emits for events in window."""
        from butlers.connectors.google_calendar import GoogleCalendarAccountRuntime

        config = self._make_config(lead_minutes=15)
        mock_mcp = MagicMock()
        mock_mcp.call_tool = AsyncMock(return_value={"status": "accepted", "request_id": "r-1"})

        runtime = GoogleCalendarAccountRuntime(config, mcp_client=mock_mcp)

        now = datetime.now(UTC)
        # Event starts in 5 minutes — overdue notification case
        event = _make_event("evt-overdue", minutes_from_now=5, now=now)
        runtime._upcoming_events["evt-overdue"] = event

        await runtime._emit_starting_soon_notifications(is_restart=True)

        mock_mcp.call_tool.assert_called_once()
        envelope = mock_mcp.call_tool.call_args[0][1]
        assert envelope["control"]["policy_tier"] == "interactive"

    async def test_dedup_prevents_double_emission(self) -> None:
        """Emitting twice for the same event in window does not duplicate."""
        from butlers.connectors.google_calendar import GoogleCalendarAccountRuntime

        config = self._make_config(lead_minutes=15)
        mock_mcp = MagicMock()
        mock_mcp.call_tool = AsyncMock(return_value={"status": "accepted", "request_id": "r-1"})

        runtime = GoogleCalendarAccountRuntime(config, mcp_client=mock_mcp)

        now = datetime.now(UTC)
        event = _make_event("evt-1", minutes_from_now=10, now=now)
        runtime._upcoming_events["evt-1"] = event

        # First call
        await runtime._emit_starting_soon_notifications(is_restart=False)
        # Second call
        await runtime._emit_starting_soon_notifications(is_restart=False)

        # Only one submission despite two scan calls
        assert mock_mcp.call_tool.call_count == 1
