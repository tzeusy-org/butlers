# Spec: REQ-dashboard-api-067
"""Invitation admission through a real migrated projection and production GET.

No hand-rolled DDL or provider requests: the production Google parser/projector
seeds the real core migration chain. Mocked router tests cannot prove its SQL,
JSONB codec, source-type/range predicate or canonical occurrence joins.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.read_models.calendar_workspace_v1 import query_calendar_invitations
from butlers.api.routers.calendar_workspace import _get_db_manager
from butlers.modules.calendar import CalendarModule, _google_event_to_calendar_event
from tests.api.auth_helpers import create_authenticated_domain_app

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
def app():
    """Use the real authenticated domain app within this integration ancestry."""
    return create_authenticated_domain_app()


async def test_migrated_invitation_projection_query_and_get(migrated_core_postgres_pool, app):
    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = start + timedelta(days=1)
    async with migrated_core_postgres_pool() as pool:
        source = await pool.fetchval(
            """INSERT INTO calendar_sources (source_key,source_kind,lane,provider,calendar_id)
               VALUES ('provider:google:primary','provider_event','user','google','primary')
               RETURNING id"""
        )
        module = CalendarModule()
        module._db = SimpleNamespace(pool=pool, db_name="general")
        module._butler_name = "general"
        events = []
        for index, status in enumerate(
            ["needsAction", "accepted", "declined", "tentative", None, "invalid"]
        ):
            attendee = {"email": "owner@example.test", "self": True}
            if status is not None:
                attendee["responseStatus"] = status
            payload = {
                "id": f"event-{index}",
                "summary": f"Case {index}",
                "start": {"dateTime": "2026-07-01T09:00:00Z"},
                "end": {"dateTime": "2026-07-01T10:00:00Z"},
                "organizer": {"email": "organizer@example.test"},
                "attendees": [attendee],
            }
            event = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
            assert event is not None
            events.append(event)
        for name in ("no-self", "self-organizer", "solo", "cancelled"):
            modified = {
                **payload,
                "id": name,
                "summary": name,
                "attendees": [
                    {"email": "owner@example.test", "self": True, "responseStatus": "needsAction"}
                ],
            }
            if name == "no-self":
                modified["attendees"][0].pop("self")
            elif name == "self-organizer":
                modified["attendees"][0]["organizer"] = True
            elif name == "solo":
                modified.pop("organizer")
            # Project the confirmed occurrence first: the unchanged Google
            # parser returns None for a cancellation, which sync passes as an ID.
            event = _google_event_to_calendar_event(modified, fallback_timezone="UTC")
            assert event is not None
            events.append(event)
        await module._project_provider_changes(
            source_id=source,
            provider_name="google",
            calendar_id="primary",
            updated_events=events,
            cancelled_ids=["cancelled"],
        )
        assert (
            _google_event_to_calendar_event(
                {**modified, "status": "cancelled"}, fallback_timezone="UTC"
            )
            is None
        )
        assert (
            await pool.fetchval("SELECT status FROM calendar_events WHERE origin_ref='cancelled'")
            == "cancelled"
        )
        duplicate_source = await pool.fetchval(
            """INSERT INTO calendar_sources (source_key,source_kind,lane,provider,calendar_id)
               VALUES ('provider:google:copy','provider_event','user','google','copy') RETURNING id"""
        )
        await module._project_provider_changes(
            source_id=duplicate_source,
            provider_name="google",
            calendar_id="copy",
            updated_events=[events[0]],
            cancelled_ids=[],
        )
        db = DatabaseManager()
        db._pools = {"general": pool}
        db._butler_modules = {"general": frozenset({"calendar"})}
        read = await query_calendar_invitations(db, start=start, end=end)
        assert len(read.rows) == 1 and read.rows[0]["origin_ref"] == "event-0"
        assert read.status_available is False and read.failed_butlers == []
        # The raw SQL window really excludes an out-of-window eligible sentinel.
        events[0].event_id = "outside-window"
        events[0].start_at += timedelta(days=2)
        events[0].end_at += timedelta(days=2)
        await module._project_provider_changes(
            source_id=source,
            provider_name="google",
            calendar_id="primary",
            updated_events=[events[0]],
            cancelled_ids=[],
        )
        app.dependency_overrides[_get_db_manager] = lambda: db
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/calendar/workspace/invitations",
                params={"start": start.isoformat(), "end": end.isoformat()},
            )
            assert response.status_code == 200
            data = response.json()["data"]
            assert len(data["entries"]) == 1
            entry = data["entries"][0]
            assert entry["organizer"] == "organizer@example.test"
            assert entry["entry_id"] == str(read.rows[0]["instance_id"])
            assert entry["conflict_issues"]
            assert all(
                any(ref["entry_id"] == entry["entry_id"] for ref in issue["events"])
                for issue in entry["conflict_issues"]
            )
            assert data["issues_available"] is False
            # Actual cancelled_ids tombstones only the newest copy. Its stale
            # other-source copy remains physically present but cannot reappear.
            await module._project_provider_changes(
                source_id=duplicate_source,
                provider_name="google",
                calendar_id="copy",
                updated_events=[],
                cancelled_ids=["event-0"],
            )
            cancelled = (
                await client.get(
                    "/api/calendar/workspace/invitations",
                    params={"start": start.isoformat(), "end": end.isoformat()},
                )
            ).json()["data"]
            assert cancelled["entries"] == [] and cancelled["issues_available"] is False
        # No fake zero/clean-empty: only eliminating the ambiguous current rows
        # from this window earns availability (synthetic disposable data only).
        await pool.execute(
            "DELETE FROM calendar_events WHERE origin_ref=ANY($1::text[])", ["event-4", "event-5"]
        )
        healthy = await query_calendar_invitations(db, start=start, end=end)
        assert healthy.rows == [] and healthy.status_available is True
        restored_event = _google_event_to_calendar_event(
            {
                **payload,
                "id": "event-0",
                "attendees": [
                    {"email": "owner@example.test", "self": True, "responseStatus": "needsAction"}
                ],
            },
            fallback_timezone="UTC",
        )
        assert restored_event is not None
        await module._project_provider_changes(
            source_id=duplicate_source,
            provider_name="google",
            calendar_id="copy",
            updated_events=[restored_event],
            cancelled_ids=[],
        )
        restored = await query_calendar_invitations(db, start=start, end=end)
        assert len(restored.rows) == 1 and restored.status_available is True
        moved_event = _google_event_to_calendar_event(
            {
                **payload,
                "id": "event-0",
                "start": {"dateTime": "2026-07-03T09:00:00Z"},
                "end": {"dateTime": "2026-07-03T10:00:00Z"},
                "attendees": [
                    {"email": "owner@example.test", "self": True, "responseStatus": "accepted"}
                ],
            },
            fallback_timezone="UTC",
        )
        assert moved_event is not None
        await module._project_provider_changes(
            source_id=duplicate_source,
            provider_name="google",
            calendar_id="copy",
            updated_events=[moved_event],
            cancelled_ids=[],
        )
        moved = await query_calendar_invitations(db, start=start, end=end)
        assert moved.rows == [] and moved.status_available is True
