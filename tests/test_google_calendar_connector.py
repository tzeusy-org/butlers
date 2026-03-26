"""Tests for Google Calendar connector sync and ingestion loop (tasks 3.1–3.6).

Covers:
- ingest.v1 envelope normalization (all event types: created, updated, deleted, starting_soon)
- syncToken cursor lifecycle (initial full sync, incremental, expired token recovery)
- Starting-soon notification logic (dedup, pruning, restart recovery)
- Event change classification
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from butlers.connectors.google_calendar import (
        GCalCursor,
        GCalSyncLoop,
        _build_ingest_envelope,
        _build_normalized_text,
        _build_starting_soon_envelope,
        _classify_event,
        _extract_organizer_email,
        _format_event_time,
    )
except ImportError:
    pytest.skip(
        "Google Calendar sync API not yet implemented (GCalCursor, GCalSyncLoop, etc.)",
        allow_module_level=True,
    )

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_ENDPOINT_IDENTITY = "google_calendar:user:test@example.com"
_ACCOUNT_EMAIL = "test@example.com"
_OBSERVED_AT = "2024-01-15T10:00:00+00:00"


def _make_event(
    event_id: str = "abc123",
    status: str = "confirmed",
    title: str = "Team Standup",
    organizer_email: str = "organizer@example.com",
    start_dt: str = "2024-01-15T09:00:00Z",
    end_dt: str = "2024-01-15T09:30:00Z",
    updated: str = "2024-01-14T08:00:00Z",
    attendees: list[dict[str, Any]] | None = None,
    location: str | None = None,
) -> dict[str, Any]:
    """Build a minimal Google Calendar event dict."""
    event: dict[str, Any] = {
        "id": event_id,
        "status": status,
        "summary": title,
        "organizer": {"email": organizer_email},
        "start": {"dateTime": start_dt},
        "end": {"dateTime": end_dt},
        "updated": updated,
    }
    if attendees is not None:
        event["attendees"] = attendees
    if location:
        event["location"] = location
    return event


def _make_all_day_event(
    event_id: str = "allday1",
    status: str = "confirmed",
    title: str = "Holiday",
    organizer_email: str = "organizer@example.com",
    start_date: str = "2024-01-15",
    end_date: str = "2024-01-16",
) -> dict[str, Any]:
    return {
        "id": event_id,
        "status": status,
        "summary": title,
        "organizer": {"email": organizer_email},
        "start": {"date": start_date},
        "end": {"date": end_date},
        "updated": "2024-01-14T08:00:00Z",
    }


def _make_sync_loop(
    cursor_pool: Any = None,
    mcp_client: Any = None,
    poll_interval_s: int = 60,
    starting_soon_lead_minutes: int = 15,
    calendar_ids: list[str] | None = None,
) -> GCalSyncLoop:
    """Create a GCalSyncLoop with mock dependencies."""
    if mcp_client is None:
        mcp_client = AsyncMock()
    return GCalSyncLoop(
        email=_ACCOUNT_EMAIL,
        endpoint_identity=_ENDPOINT_IDENTITY,
        client_id="test-client-id",
        client_secret="test-client-secret",
        refresh_token="test-refresh-token",
        cursor_pool=cursor_pool,
        mcp_client=mcp_client,
        poll_interval_s=poll_interval_s,
        starting_soon_lead_minutes=starting_soon_lead_minutes,
        calendar_ids=calendar_ids or ["primary"],
    )


# ---------------------------------------------------------------------------
# 3.4 Event change classification
# ---------------------------------------------------------------------------


class TestClassifyEvent:
    """Tests for _classify_event (task 3.4)."""

    def test_cancelled_event_is_deleted(self) -> None:
        event = _make_event(status="cancelled")
        assert _classify_event(event, is_initial_sync=False) == "event_deleted"
        assert _classify_event(event, is_initial_sync=True) == "event_deleted"

    def test_confirmed_event_on_incremental_is_updated(self) -> None:
        event = _make_event(status="confirmed")
        assert _classify_event(event, is_initial_sync=False) == "event_updated"

    def test_confirmed_event_on_initial_is_created(self) -> None:
        event = _make_event(status="confirmed")
        assert _classify_event(event, is_initial_sync=True) == "event_created"

    def test_tentative_event_on_incremental_is_updated(self) -> None:
        event = _make_event(status="tentative")
        assert _classify_event(event, is_initial_sync=False) == "event_updated"

    def test_empty_status_on_incremental_is_updated(self) -> None:
        event = {"id": "x", "summary": "No status field"}
        assert _classify_event(event, is_initial_sync=False) == "event_updated"


# ---------------------------------------------------------------------------
# 3.5 Envelope normalization
# ---------------------------------------------------------------------------


class TestBuildIngestEnvelope:
    """Tests for _build_ingest_envelope (task 3.5)."""

    def test_created_envelope_fields(self) -> None:
        event = _make_event(
            event_id="ev1",
            title="Planning",
            organizer_email="boss@corp.com",
            updated="2024-01-15T08:00:00Z",
        )
        env = _build_ingest_envelope(
            event,
            "event_created",
            _ENDPOINT_IDENTITY,
            _ACCOUNT_EMAIL,
            _OBSERVED_AT,
        )

        assert env["source"]["channel"] == "google_calendar"
        assert env["source"]["provider"] == "google_calendar"
        assert env["source"]["endpoint_identity"] == _ENDPOINT_IDENTITY
        assert env["event"]["event_type"] == "event_created"
        assert env["event"]["external_event_id"] == "ev1"
        assert env["event"]["external_thread_id"] == "ev1"
        assert env["event"]["observed_at"] == _OBSERVED_AT
        assert env["sender"]["identity"] == "boss@corp.com"
        assert env["control"]["ingestion_tier"] == "full"
        assert env["control"]["policy_tier"] == "default"
        assert env["payload"]["raw"] is event

    def test_idempotency_key_format(self) -> None:
        event = _make_event(event_id="ev2", updated="2024-01-15T09:00:00Z")
        env = _build_ingest_envelope(
            event, "event_updated", _ENDPOINT_IDENTITY, _ACCOUNT_EMAIL, _OBSERVED_AT
        )
        expected_key = f"gcal:{_ENDPOINT_IDENTITY}:ev2:2024-01-15T09:00:00Z"
        assert env["control"]["idempotency_key"] == expected_key

    def test_deleted_envelope_type(self) -> None:
        event = _make_event(event_id="ev3", status="cancelled")
        env = _build_ingest_envelope(
            event, "event_deleted", _ENDPOINT_IDENTITY, _ACCOUNT_EMAIL, _OBSERVED_AT
        )
        assert env["event"]["event_type"] == "event_deleted"
        assert env["control"]["policy_tier"] == "default"

    def test_organizer_fallback_to_account_email(self) -> None:
        event = _make_event(event_id="ev4", organizer_email="")
        event["organizer"] = {}
        env = _build_ingest_envelope(
            event, "event_updated", _ENDPOINT_IDENTITY, _ACCOUNT_EMAIL, _OBSERVED_AT
        )
        assert env["sender"]["identity"] == _ACCOUNT_EMAIL.lower()

    def test_normalized_text_included(self) -> None:
        event = _make_event(event_id="ev5", title="Sprint Review")
        env = _build_ingest_envelope(
            event, "event_updated", _ENDPOINT_IDENTITY, _ACCOUNT_EMAIL, _OBSERVED_AT
        )
        assert "[Calendar: updated]" in env["payload"]["normalized_text"]
        assert "Sprint Review" in env["payload"]["normalized_text"]

    def test_updated_timestamp_fallback(self) -> None:
        """When event lacks 'updated', observed_at is used in idempotency key."""
        event = {"id": "ev6", "summary": "No update ts"}
        env = _build_ingest_envelope(
            event, "event_updated", _ENDPOINT_IDENTITY, _ACCOUNT_EMAIL, _OBSERVED_AT
        )
        assert f"gcal:{_ENDPOINT_IDENTITY}:ev6:{_OBSERVED_AT}" == env["control"]["idempotency_key"]


class TestBuildStartingSoonEnvelope:
    """Tests for _build_starting_soon_envelope (task 3.5 starting_soon variant)."""

    def test_starting_soon_envelope_fields(self) -> None:
        event = _make_event(event_id="ev10", title="Kick-off")
        env = _build_starting_soon_envelope(
            event, _ENDPOINT_IDENTITY, _ACCOUNT_EMAIL, _OBSERVED_AT, lead_minutes=15
        )

        assert env["event"]["event_type"] == "event_starting_soon"
        assert env["event"]["external_event_id"] == "starting_soon:ev10"
        # thread_id is the original event id
        assert env["event"]["external_thread_id"] == "ev10"
        assert env["control"]["policy_tier"] == "interactive"
        assert env["control"]["ingestion_tier"] == "full"

    def test_starting_soon_idempotency_key(self) -> None:
        event = _make_event(event_id="ev11")
        env = _build_starting_soon_envelope(
            event, _ENDPOINT_IDENTITY, _ACCOUNT_EMAIL, _OBSERVED_AT, lead_minutes=15
        )
        expected = f"gcal:{_ENDPOINT_IDENTITY}:starting_soon:ev11:15"
        assert env["control"]["idempotency_key"] == expected

    def test_starting_soon_lead_minutes_in_key(self) -> None:
        event = _make_event(event_id="ev12")
        env30 = _build_starting_soon_envelope(
            event, _ENDPOINT_IDENTITY, _ACCOUNT_EMAIL, _OBSERVED_AT, lead_minutes=30
        )
        env15 = _build_starting_soon_envelope(
            event, _ENDPOINT_IDENTITY, _ACCOUNT_EMAIL, _OBSERVED_AT, lead_minutes=15
        )
        assert ":30" in env30["control"]["idempotency_key"]
        assert ":15" in env15["control"]["idempotency_key"]


class TestBuildNormalizedText:
    """Tests for _build_normalized_text."""

    def test_format_created(self) -> None:
        event = _make_event(title="Team Meeting", location="Room A", attendees=[{"email": "a@b.c"}])
        text = _build_normalized_text(event, "event_created")
        assert text.startswith("[Calendar: created]")
        assert "Team Meeting" in text
        assert "Room A" in text
        assert "1 attendees" in text

    def test_format_deleted(self) -> None:
        event = _make_event(title="Cancelled")
        text = _build_normalized_text(event, "event_deleted")
        assert "[Calendar: deleted]" in text
        assert "Cancelled" in text

    def test_format_starting_soon(self) -> None:
        event = _make_event(title="Standup")
        text = _build_normalized_text(event, "event_starting_soon")
        assert "[Calendar: starting_soon]" in text

    def test_no_location_omitted(self) -> None:
        event = _make_event(title="Quick sync")
        text = _build_normalized_text(event, "event_updated")
        # Location segment should not appear
        assert "| |" not in text  # no empty location segment

    def test_zero_attendees(self) -> None:
        event = _make_event(title="Solo")
        event.pop("attendees", None)
        text = _build_normalized_text(event, "event_updated")
        assert "0 attendees" in text


# ---------------------------------------------------------------------------
# Cursor model
# ---------------------------------------------------------------------------


class TestGCalCursor:
    """Tests for GCalCursor serialization."""

    def test_round_trip_json(self) -> None:
        cursor = GCalCursor(
            sync_token="mytoken123",
            last_updated_at="2024-01-15T10:00:00+00:00",
        )
        raw = cursor.to_json()
        restored = GCalCursor.from_json(raw)
        assert restored.sync_token == cursor.sync_token
        assert restored.last_updated_at == cursor.last_updated_at

    def test_from_json_invalid_raises(self) -> None:
        with pytest.raises(Exception):
            GCalCursor.from_json("not valid json")


# ---------------------------------------------------------------------------
# 3.1 Initial full sync
# ---------------------------------------------------------------------------


class TestInitialFullSync:
    """Tests for initial full sync (task 3.1)."""

    async def test_initial_sync_skips_ingestion(self) -> None:
        """Full sync with no cursor should NOT ingest events."""
        mcp_client = AsyncMock()
        mcp_client.call_tool = AsyncMock()
        cursor_pool = MagicMock()
        loop = _make_sync_loop(cursor_pool=cursor_pool, mcp_client=mcp_client)

        # Mock load_cursor → None (no prior cursor)
        # Mock save_cursor → success
        events = [_make_event(event_id=f"ev{i}") for i in range(3)]
        response = {"items": events, "nextSyncToken": "token-xyz"}

        import httpx

        with (
            patch(
                "butlers.connectors.google_calendar.load_cursor",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.connectors.google_calendar.save_cursor",
                new=AsyncMock(),
            ),
            patch.object(
                loop,
                "_ensure_access_token",
                new=AsyncMock(return_value="access-token"),
            ),
        ):
            # Patch _list_events to return our fixture response
            with patch.object(loop, "_list_events", new=AsyncMock(return_value=response)):
                await loop.run_once(httpx.AsyncClient(), "primary")

        # MCP ingest should NOT have been called
        mcp_client.call_tool.assert_not_called()

    async def test_initial_sync_persists_sync_token(self) -> None:
        """After initial full sync, the nextSyncToken should be saved."""
        cursor_pool = MagicMock()
        loop = _make_sync_loop(cursor_pool=cursor_pool)

        events = [_make_event()]
        response = {"items": events, "nextSyncToken": "initial-token"}

        saved_cursors: list[str] = []

        async def _fake_save(pool: Any, connector_type: str, endpoint: str, value: str) -> None:
            saved_cursors.append(value)

        import httpx

        with (
            patch(
                "butlers.connectors.google_calendar.load_cursor",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.connectors.google_calendar.save_cursor",
                new=AsyncMock(side_effect=_fake_save),
            ),
            patch.object(loop, "_list_events", new=AsyncMock(return_value=response)),
        ):
            await loop.run_once(httpx.AsyncClient(), "primary")

        assert len(saved_cursors) == 1
        saved = GCalCursor.from_json(saved_cursors[0])
        assert saved.sync_token == "initial-token"

    async def test_initial_sync_pagination(self) -> None:
        """Full sync should page through all results before saving cursor."""
        cursor_pool = MagicMock()
        saved_cursors: list[str] = []

        async def _fake_save(pool: Any, connector_type: str, endpoint: str, value: str) -> None:
            saved_cursors.append(value)

        loop = _make_sync_loop(cursor_pool=cursor_pool)

        page1 = {"items": [_make_event("p1e1")], "nextPageToken": "page2"}
        page2 = {"items": [_make_event("p1e2")], "nextSyncToken": "final-token"}

        responses = iter([page1, page2])

        async def _fake_list_events(
            client: Any,
            calendar_id: str,
            *,
            sync_token: str | None = None,
            page_token: str | None = None,
            max_retries: int = 3,
        ) -> dict[str, Any]:
            return next(responses)

        import httpx

        with (
            patch(
                "butlers.connectors.google_calendar.load_cursor",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.connectors.google_calendar.save_cursor",
                new=AsyncMock(side_effect=_fake_save),
            ),
            patch.object(loop, "_list_events", new=AsyncMock(side_effect=_fake_list_events)),
        ):
            await loop.run_once(httpx.AsyncClient(), "primary")

        assert len(saved_cursors) == 1
        saved = GCalCursor.from_json(saved_cursors[0])
        assert saved.sync_token == "final-token"


# ---------------------------------------------------------------------------
# 3.2 Incremental sync
# ---------------------------------------------------------------------------


class TestIncrementalSync:
    """Tests for incremental sync poll loop (task 3.2)."""

    async def test_incremental_sync_ingests_changed_events(self) -> None:
        """Incremental sync should submit each changed event as an ingest envelope."""
        mcp_client = AsyncMock()
        mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        cursor_pool = MagicMock()
        loop = _make_sync_loop(cursor_pool=cursor_pool, mcp_client=mcp_client)

        existing_cursor = GCalCursor(sync_token="old-token", last_updated_at="2024-01-14T00:00:00Z")
        changed_events = [_make_event(f"ev{i}") for i in range(3)]
        response = {"items": changed_events, "nextSyncToken": "new-token"}

        saved_cursors: list[str] = []

        async def _fake_save(pool: Any, connector_type: str, endpoint: str, value: str) -> None:
            saved_cursors.append(value)

        import httpx

        with (
            patch(
                "butlers.connectors.google_calendar.load_cursor",
                new=AsyncMock(return_value=existing_cursor.to_json()),
            ),
            patch(
                "butlers.connectors.google_calendar.save_cursor",
                new=AsyncMock(side_effect=_fake_save),
            ),
            patch.object(loop, "_list_events", new=AsyncMock(return_value=response)),
        ):
            await loop.run_once(httpx.AsyncClient(), "primary")

        # Should have submitted 3 envelopes
        assert mcp_client.call_tool.call_count == 3
        # Cursor should have advanced
        assert len(saved_cursors) == 1
        saved = GCalCursor.from_json(saved_cursors[0])
        assert saved.sync_token == "new-token"

    async def test_incremental_sync_pagination_submits_all_events(self) -> None:
        """All pages of incremental sync should be ingested before cursor advances."""
        mcp_client = AsyncMock()
        mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        cursor_pool = MagicMock()
        loop = _make_sync_loop(cursor_pool=cursor_pool, mcp_client=mcp_client)

        existing_cursor = GCalCursor(sync_token="old-token", last_updated_at="2024-01-14T00:00:00Z")

        page1 = {"items": [_make_event("p1e1"), _make_event("p1e2")], "nextPageToken": "page2"}
        page2 = {"items": [_make_event("p2e1")], "nextSyncToken": "paged-token"}

        responses = iter([page1, page2])

        async def _fake_list(
            client: Any,
            calendar_id: str,
            *,
            sync_token: str | None = None,
            page_token: str | None = None,
            max_retries: int = 3,
        ) -> dict[str, Any]:
            return next(responses)

        saved_cursors: list[str] = []

        async def _fake_save(pool: Any, connector_type: str, endpoint: str, value: str) -> None:
            saved_cursors.append(value)

        import httpx

        with (
            patch(
                "butlers.connectors.google_calendar.load_cursor",
                new=AsyncMock(return_value=existing_cursor.to_json()),
            ),
            patch(
                "butlers.connectors.google_calendar.save_cursor",
                new=AsyncMock(side_effect=_fake_save),
            ),
            patch.object(loop, "_list_events", new=AsyncMock(side_effect=_fake_list)),
        ):
            await loop.run_once(httpx.AsyncClient(), "primary")

        # 3 events across 2 pages
        assert mcp_client.call_tool.call_count == 3
        saved = GCalCursor.from_json(saved_cursors[0])
        assert saved.sync_token == "paged-token"

    async def test_no_changes_preserves_cursor(self) -> None:
        """Empty incremental sync still advances cursor to new nextSyncToken."""
        mcp_client = AsyncMock()
        cursor_pool = MagicMock()
        loop = _make_sync_loop(cursor_pool=cursor_pool, mcp_client=mcp_client)

        existing_cursor = GCalCursor(
            sync_token="stable-token", last_updated_at="2024-01-14T00:00:00Z"
        )
        response = {"items": [], "nextSyncToken": "still-stable-token"}

        saved_cursors: list[str] = []

        async def _fake_save(pool: Any, connector_type: str, endpoint: str, value: str) -> None:
            saved_cursors.append(value)

        import httpx

        with (
            patch(
                "butlers.connectors.google_calendar.load_cursor",
                new=AsyncMock(return_value=existing_cursor.to_json()),
            ),
            patch(
                "butlers.connectors.google_calendar.save_cursor",
                new=AsyncMock(side_effect=_fake_save),
            ),
            patch.object(loop, "_list_events", new=AsyncMock(return_value=response)),
        ):
            await loop.run_once(httpx.AsyncClient(), "primary")

        mcp_client.call_tool.assert_not_called()
        assert len(saved_cursors) == 1
        saved = GCalCursor.from_json(saved_cursors[0])
        assert saved.sync_token == "still-stable-token"


# ---------------------------------------------------------------------------
# 3.3 Expired syncToken handling
# ---------------------------------------------------------------------------


class TestExpiredSyncToken:
    """Tests for expired syncToken handling (task 3.3)."""

    async def test_410_triggers_full_resync_with_ingestion(self) -> None:
        """HTTP 410 on incremental sync should trigger full resync that ingests events."""
        import httpx

        mcp_client = AsyncMock()
        mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        cursor_pool = MagicMock()
        loop = _make_sync_loop(cursor_pool=cursor_pool, mcp_client=mcp_client)

        existing_cursor = GCalCursor(
            sync_token="expired-token", last_updated_at="2024-01-14T00:00:00Z"
        )

        # The incremental sync returns a 410
        async def _list_events_410(
            client: Any,
            calendar_id: str,
            *,
            sync_token: str | None = None,
            page_token: str | None = None,
            max_retries: int = 3,
        ) -> dict[str, Any]:
            if sync_token:
                # Build a mock 410 response
                mock_resp = MagicMock(spec=httpx.Response)
                mock_resp.status_code = 410
                raise httpx.HTTPStatusError("Gone", request=MagicMock(), response=mock_resp)
            # Full sync (no syncToken) — return some events
            return {
                "items": [_make_event("after-resync-1"), _make_event("after-resync-2")],
                "nextSyncToken": "fresh-token-after-resync",
            }

        saved_cursors: list[str] = []

        async def _fake_save(pool: Any, connector_type: str, endpoint: str, value: str) -> None:
            saved_cursors.append(value)

        with (
            patch(
                "butlers.connectors.google_calendar.load_cursor",
                new=AsyncMock(return_value=existing_cursor.to_json()),
            ),
            patch(
                "butlers.connectors.google_calendar.save_cursor",
                new=AsyncMock(side_effect=_fake_save),
            ),
            patch.object(loop, "_list_events", new=AsyncMock(side_effect=_list_events_410)),
        ):
            await loop.run_once(httpx.AsyncClient(), "primary")

        # Recovery full sync DOES ingest events
        assert mcp_client.call_tool.call_count == 2
        assert len(saved_cursors) == 1
        saved = GCalCursor.from_json(saved_cursors[0])
        assert saved.sync_token == "fresh-token-after-resync"

    async def test_initial_full_sync_does_not_ingest_on_fresh_start(self) -> None:
        """Initial full sync (no prior cursor) must not ingest events."""
        mcp_client = AsyncMock()
        mcp_client.call_tool = AsyncMock()
        cursor_pool = MagicMock()
        loop = _make_sync_loop(cursor_pool=cursor_pool, mcp_client=mcp_client)

        import httpx

        response = {
            "items": [_make_event("baseline-1"), _make_event("baseline-2")],
            "nextSyncToken": "baseline-token",
        }

        with (
            patch(
                "butlers.connectors.google_calendar.load_cursor",
                new=AsyncMock(return_value=None),  # No existing cursor
            ),
            patch("butlers.connectors.google_calendar.save_cursor", new=AsyncMock()),
            patch.object(loop, "_list_events", new=AsyncMock(return_value=response)),
        ):
            await loop.run_once(httpx.AsyncClient(), "primary")

        mcp_client.call_tool.assert_not_called()


# ---------------------------------------------------------------------------
# 3.6 Checkpoint-after-acceptance
# ---------------------------------------------------------------------------


class TestCheckpointAfterAcceptance:
    """Tests for checkpoint-after-acceptance cursor advancement (task 3.6)."""

    async def test_cursor_not_advanced_on_ingest_failure(self) -> None:
        """If ingest fails, the cursor should not advance."""
        mcp_client = AsyncMock()
        # call_tool raises an exception (ingest failure)
        mcp_client.call_tool = AsyncMock(side_effect=RuntimeError("Switchboard down"))
        cursor_pool = MagicMock()
        loop = _make_sync_loop(cursor_pool=cursor_pool, mcp_client=mcp_client)

        existing_cursor = GCalCursor(
            sync_token="safe-token", last_updated_at="2024-01-14T00:00:00Z"
        )
        response = {"items": [_make_event("ev-fail")], "nextSyncToken": "new-token"}

        saved_cursors: list[str] = []

        async def _fake_save(pool: Any, connector_type: str, endpoint: str, value: str) -> None:
            saved_cursors.append(value)

        import httpx

        with (
            patch(
                "butlers.connectors.google_calendar.load_cursor",
                new=AsyncMock(return_value=existing_cursor.to_json()),
            ),
            patch(
                "butlers.connectors.google_calendar.save_cursor",
                new=AsyncMock(side_effect=_fake_save),
            ),
            patch.object(loop, "_list_events", new=AsyncMock(return_value=response)),
        ):
            await loop.run_once(httpx.AsyncClient(), "primary")

        # Cursor should NOT have been saved because ingest failed
        assert len(saved_cursors) == 0

    async def test_cursor_advanced_after_all_events_accepted(self) -> None:
        """Cursor must advance only after all events are successfully ingested."""
        mcp_client = AsyncMock()
        mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})
        cursor_pool = MagicMock()
        loop = _make_sync_loop(cursor_pool=cursor_pool, mcp_client=mcp_client)

        existing_cursor = GCalCursor(
            sync_token="base-token", last_updated_at="2024-01-14T00:00:00Z"
        )
        events = [_make_event(f"ev{i}") for i in range(5)]
        response = {"items": events, "nextSyncToken": "advanced-token"}

        saved_cursors: list[str] = []

        async def _fake_save(pool: Any, connector_type: str, endpoint: str, value: str) -> None:
            saved_cursors.append(value)

        import httpx

        with (
            patch(
                "butlers.connectors.google_calendar.load_cursor",
                new=AsyncMock(return_value=existing_cursor.to_json()),
            ),
            patch(
                "butlers.connectors.google_calendar.save_cursor",
                new=AsyncMock(side_effect=_fake_save),
            ),
            patch.object(loop, "_list_events", new=AsyncMock(return_value=response)),
        ):
            await loop.run_once(httpx.AsyncClient(), "primary")

        assert len(saved_cursors) == 1
        saved = GCalCursor.from_json(saved_cursors[0])
        assert saved.sync_token == "advanced-token"


# ---------------------------------------------------------------------------
# Starting soon notification logic
# ---------------------------------------------------------------------------


class TestStartingSoonNotifications:
    """Tests for starting-soon notification synthesis."""

    async def _run_check_starting_soon(
        self, loop: GCalSyncLoop, upcoming_events: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Helper to run _check_starting_soon and capture submitted envelopes."""
        loop._upcoming_events = upcoming_events
        submitted: list[dict[str, Any]] = []

        async def _fake_submit(envelope: dict[str, Any]) -> bool:
            submitted.append(envelope)
            return True

        loop._submit_envelope = _fake_submit  # type: ignore[method-assign]
        await loop._check_starting_soon()
        return submitted

    async def test_event_within_window_triggers_notification(self) -> None:
        """An event starting within the lead window should trigger a notification."""
        loop = _make_sync_loop(starting_soon_lead_minutes=15)
        now = datetime.now(UTC)
        start = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        end = (now + timedelta(minutes=35)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        event = _make_event("ev1", start_dt=start, end_dt=end)

        submitted = await self._run_check_starting_soon(loop, {"ev1": event})

        assert len(submitted) == 1
        assert submitted[0]["event"]["event_type"] == "event_starting_soon"

    async def test_event_outside_window_no_notification(self) -> None:
        """An event outside the lead window should not trigger a notification."""
        loop = _make_sync_loop(starting_soon_lead_minutes=15)
        now = datetime.now(UTC)
        # Event starts 30 minutes from now, outside 15-minute window
        start = (now + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        end = (now + timedelta(minutes=60)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        event = _make_event("ev2", start_dt=start, end_dt=end)

        submitted = await self._run_check_starting_soon(loop, {"ev2": event})

        assert len(submitted) == 0

    async def test_deduplication_prevents_duplicate_notifications(self) -> None:
        """An event should only trigger one notification even if checked multiple times."""
        loop = _make_sync_loop(starting_soon_lead_minutes=15)
        now = datetime.now(UTC)
        start = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        end = (now + timedelta(minutes=35)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        event = _make_event("ev3", start_dt=start, end_dt=end)

        submitted1 = await self._run_check_starting_soon(loop, {"ev3": event})
        # Second check with same event — seen-set should prevent duplicate
        submitted2 = await self._run_check_starting_soon(loop, {"ev3": event})

        assert len(submitted1) == 1
        assert len(submitted2) == 0

    async def test_past_events_pruned_from_cache(self) -> None:
        """Events that have already started are pruned from the upcoming cache."""
        loop = _make_sync_loop(starting_soon_lead_minutes=15)
        now = datetime.now(UTC)
        # Event started 30 minutes ago
        start = (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        end = (now - timedelta(minutes=0)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        event = _make_event("past-ev", start_dt=start, end_dt=end)
        loop._upcoming_events = {"past-ev": event}

        submitted: list[dict[str, Any]] = []

        async def _fake_submit(envelope: dict[str, Any]) -> bool:
            submitted.append(envelope)
            return True

        loop._submit_envelope = _fake_submit  # type: ignore[method-assign]
        await loop._check_starting_soon()

        # Past event should be pruned
        assert "past-ev" not in loop._upcoming_events
        # No notification for past event
        assert len(submitted) == 0

    async def test_restart_recovery_emits_overdue_notifications(self) -> None:
        """On restart, events in the window but not yet started should fire."""
        loop = _make_sync_loop(starting_soon_lead_minutes=15)
        # No prior seen-set entries (simulating restart)
        loop._starting_soon_seen.clear()

        now = datetime.now(UTC)
        start = (now + timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        end = (now + timedelta(minutes=33)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        event = _make_event("restart-ev", start_dt=start, end_dt=end)
        loop._upcoming_events = {"restart-ev": event}

        submitted: list[dict[str, Any]] = []

        async def _fake_submit(envelope: dict[str, Any]) -> bool:
            submitted.append(envelope)
            return True

        loop._submit_envelope = _fake_submit  # type: ignore[method-assign]
        await loop._check_starting_soon()

        assert len(submitted) == 1
        assert submitted[0]["event"]["event_type"] == "event_starting_soon"

    async def test_lead_minutes_zero_disables_notifications(self) -> None:
        """Setting lead_minutes=0 should completely disable starting-soon notifications."""
        loop = _make_sync_loop(starting_soon_lead_minutes=0)
        now = datetime.now(UTC)
        start = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        end = (now + timedelta(minutes=35)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        event = _make_event("ev-disabled", start_dt=start, end_dt=end)

        submitted = await self._run_check_starting_soon(loop, {"ev-disabled": event})
        assert len(submitted) == 0

    async def test_all_day_events_skipped(self) -> None:
        """All-day events (date-only) should not trigger starting-soon notifications."""
        loop = _make_sync_loop(starting_soon_lead_minutes=15)
        event = _make_all_day_event("allday1")
        # The start time is a date-only format — _check_starting_soon should skip it
        loop._upcoming_events = {"allday1": event}

        submitted: list[dict[str, Any]] = []

        async def _fake_submit(envelope: dict[str, Any]) -> bool:
            submitted.append(envelope)
            return True

        loop._submit_envelope = _fake_submit  # type: ignore[method-assign]
        await loop._check_starting_soon()

        assert len(submitted) == 0


# ---------------------------------------------------------------------------
# GCalProcessConfig
# ---------------------------------------------------------------------------


class TestGCalProcessConfig:
    """Tests for GCalProcessConfig.from_env."""

    def test_from_env_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from butlers.connectors.google_calendar import GCalProcessConfig

        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.delenv("GCAL_POLL_INTERVAL_S", raising=False)
        monkeypatch.delenv("GCAL_STARTING_SOON_LEAD_MINUTES", raising=False)
        monkeypatch.delenv("GCAL_ACCOUNT_RESCAN_INTERVAL_S", raising=False)

        cfg = GCalProcessConfig.from_env()
        assert cfg.switchboard_mcp_url == "http://localhost:41100/sse"
        assert cfg.gcal_poll_interval_s == 60
        assert cfg.gcal_starting_soon_lead_minutes == 15
        assert cfg.gcal_account_rescan_interval_s == 300

    def test_from_env_missing_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from butlers.connectors.google_calendar import GCalProcessConfig

        monkeypatch.delenv("SWITCHBOARD_MCP_URL", raising=False)
        with pytest.raises(KeyError):
            GCalProcessConfig.from_env()

    def test_from_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from butlers.connectors.google_calendar import GCalProcessConfig

        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("GCAL_POLL_INTERVAL_S", "30")
        monkeypatch.setenv("GCAL_STARTING_SOON_LEAD_MINUTES", "10")
        monkeypatch.setenv("GCAL_ACCOUNT_RESCAN_INTERVAL_S", "120")

        cfg = GCalProcessConfig.from_env()
        assert cfg.gcal_poll_interval_s == 30
        assert cfg.gcal_starting_soon_lead_minutes == 10
        assert cfg.gcal_account_rescan_interval_s == 120

    def test_from_env_invalid_integer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from butlers.connectors.google_calendar import GCalProcessConfig

        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("GCAL_POLL_INTERVAL_S", "not-a-number")

        with pytest.raises(ValueError, match="GCAL_POLL_INTERVAL_S must be an integer"):
            GCalProcessConfig.from_env()


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestHelperFunctions:
    """Tests for utility functions."""

    def test_extract_organizer_email_present(self) -> None:
        event = {"organizer": {"email": "Org@example.COM"}}
        result = _extract_organizer_email(event, "fallback@example.com")
        assert result == "org@example.com"

    def test_extract_organizer_email_fallback(self) -> None:
        event = {"organizer": {}}
        result = _extract_organizer_email(event, "fallback@example.com")
        assert result == "fallback@example.com"

    def test_extract_organizer_email_missing_organizer(self) -> None:
        result = _extract_organizer_email({}, "fallback@example.com")
        assert result == "fallback@example.com"

    def test_format_event_time_datetime(self) -> None:
        time_obj = {"dateTime": "2024-01-15T09:00:00Z"}
        assert _format_event_time(time_obj) == "2024-01-15T09:00:00Z"

    def test_format_event_time_date_only(self) -> None:
        time_obj = {"date": "2024-01-15"}
        assert _format_event_time(time_obj) == "2024-01-15"

    def test_format_event_time_none(self) -> None:
        assert _format_event_time(None) == "unknown"

    def test_format_event_time_empty(self) -> None:
        assert _format_event_time({}) == "unknown"
