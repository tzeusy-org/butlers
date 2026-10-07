"""Tests for the situational context-bus producers (RFC 0009, bu-hmdqz.15).

Two layers:
- Pure-logic unit tests for deterministic classifiers and the shared Owner
  Attention Policy expiry anchor.
- Docker-gated integration tests that run each producer against a real,
  migration-accurate Postgres and assert it writes/clears ``public.user_context``
  correctly — including the closed-loop verification that the notify gate's
  ``get_suppressing_context_signal`` now sees a live ``sleeping`` signal, i.e.
  the previously-dark ``suppressed_context_bus`` branch is reachable.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from butlers.chronicler.adapters.owntracks_place_cluster import PlaceReference, haversine_meters
from butlers.context_bus import ContextSignal
from butlers.core.state import state_set
from butlers.db import register_jsonb_codec
from butlers.jobs.context_producers import (
    classify_calendar_signal,
    resolve_commuting_eta,
    resolve_owner_presence,
    resolve_owner_room,
    run_calendar_context_producer,
    run_commuting_eta_context_producer,
    run_home_presence_context_producer,
    run_sleep_window_context_producer,
    run_travel_context_producer,
)
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

docker_available = shutil.which("docker") is not None


# ---------------------------------------------------------------------------
# Pure-logic unit tests (no DB)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Standup", ContextSignal.meeting),
        ("1:1 with Alex", ContextSignal.meeting),
        (None, ContextSignal.meeting),
        ("", ContextSignal.meeting),
        ("Focus block", ContextSignal.focused),
        ("Deep Work — spec", ContextSignal.focused),
        ("heads-down time", ContextSignal.focused),
        ("No meetings please", ContextSignal.focused),
        ("Do Not Disturb", ContextSignal.focused),
    ],
)
def test_classify_calendar_signal(title, expected):
    assert classify_calendar_signal(title) is expected


def test_resolve_owner_presence():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    fresh = now - timedelta(minutes=5)
    stale = now - timedelta(hours=2)
    owner_ids = frozenset({"person.owner", "device_tracker.owner_phone"})

    # A fresh owner-linked entity reading "home" -> True
    assert (
        resolve_owner_presence(
            [{"entity_id": "person.owner", "state": "home", "last_updated": fresh}],
            owner_entity_ids=owner_ids,
            now=now,
        )
        is True
    )
    # Fresh owner-linked entities, none home -> False (explicit away)
    assert (
        resolve_owner_presence(
            [
                {
                    "entity_id": "device_tracker.owner_phone",
                    "state": "not_home",
                    "last_updated": fresh,
                }
            ],
            owner_entity_ids=owner_ids,
            now=now,
        )
        is False
    )
    # Only a stale "home" reading -> unknown (never assert on a dead feed)
    assert (
        resolve_owner_presence(
            [{"entity_id": "person.owner", "state": "home", "last_updated": stale}],
            owner_entity_ids=owner_ids,
            now=now,
        )
        is None
    )
    # A fresh, non-owner entity (housemate/guest) reading "home" is ignored -> unknown,
    # never asserts at_home on the owner's behalf.
    assert (
        resolve_owner_presence(
            [{"entity_id": "person.housemate", "state": "home", "last_updated": fresh}],
            owner_entity_ids=owner_ids,
            now=now,
        )
        is None
    )
    # Owner absent while a housemate is fresh-and-home -> still False, not True: the
    # housemate's presence must never stand in for the owner's.
    assert (
        resolve_owner_presence(
            [
                {"entity_id": "person.housemate", "state": "home", "last_updated": fresh},
                {"entity_id": "person.owner", "state": "not_home", "last_updated": fresh},
            ],
            owner_entity_ids=owner_ids,
            now=now,
        )
        is False
    )
    # Empty -> unknown
    assert resolve_owner_presence([], owner_entity_ids=owner_ids, now=now) is None
    # A fresh home reading wins even when another owner entity is away
    assert (
        resolve_owner_presence(
            [
                {
                    "entity_id": "device_tracker.owner_phone",
                    "state": "not_home",
                    "last_updated": fresh,
                },
                {"entity_id": "person.owner", "state": "home", "last_updated": fresh},
            ],
            owner_entity_ids=owner_ids,
            now=now,
        )
        is True
    )


def test_resolve_owner_room():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    fresh = now - timedelta(minutes=5)
    stale = now - timedelta(hours=2)
    owner_ids = frozenset({"person.owner", "device_tracker.owner_phone"})

    # A zone-aware device tracker reports the room directly as its state.
    assert (
        resolve_owner_room(
            [
                {
                    "entity_id": "device_tracker.owner_phone",
                    "state": "office",
                    "last_updated": fresh,
                    "attributes": None,
                }
            ],
            owner_entity_ids=owner_ids,
            now=now,
        )
        == "office"
    )
    # A generic "home" state falls back to Home Assistant area attributes.
    assert (
        resolve_owner_room(
            [
                {
                    "entity_id": "person.owner",
                    "state": "home",
                    "last_updated": fresh,
                    "attributes": {"area": "kitchen"},
                }
            ],
            owner_entity_ids=owner_ids,
            now=now,
        )
        == "kitchen"
    )
    # No area data anywhere (state is generic, attributes empty) -> unknown.
    assert (
        resolve_owner_room(
            [
                {
                    "entity_id": "person.owner",
                    "state": "home",
                    "last_updated": fresh,
                    "attributes": {},
                }
            ],
            owner_entity_ids=owner_ids,
            now=now,
        )
        is None
    )
    # Only a stale room reading -> unknown (never report a dead feed's room).
    assert (
        resolve_owner_room(
            [
                {
                    "entity_id": "device_tracker.owner_phone",
                    "state": "office",
                    "last_updated": stale,
                    "attributes": None,
                }
            ],
            owner_entity_ids=owner_ids,
            now=now,
        )
        is None
    )
    # A fresh non-owner entity's room is ignored -> unknown.
    assert (
        resolve_owner_room(
            [
                {
                    "entity_id": "device_tracker.housemate_phone",
                    "state": "kitchen",
                    "last_updated": fresh,
                    "attributes": None,
                }
            ],
            owner_entity_ids=owner_ids,
            now=now,
        )
        is None
    )
    # Multiple fresh owner rows -> the most recently updated one wins.
    assert (
        resolve_owner_room(
            [
                {
                    "entity_id": "person.owner",
                    "state": "office",
                    "last_updated": fresh - timedelta(minutes=1),
                    "attributes": None,
                },
                {
                    "entity_id": "device_tracker.owner_phone",
                    "state": "kitchen",
                    "last_updated": fresh,
                    "attributes": None,
                },
            ],
            owner_entity_ids=owner_ids,
            now=now,
        )
        == "kitchen"
    )
    # Empty -> unknown
    assert resolve_owner_room([], owner_entity_ids=owner_ids, now=now) is None


_HOME_REFERENCE = PlaceReference(label="home", lat=1.3, lon=103.8, radius_m=150.0)


def test_resolve_commuting_eta_no_fresh_points_is_ambiguous():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert resolve_commuting_eta([], home=_HOME_REFERENCE, now=now) is None


def test_resolve_commuting_eta_ignores_stale_points():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    stale = now - timedelta(minutes=45)
    points = [{"ts": stale, "lat": 1.32, "lon": 103.82}]
    assert resolve_commuting_eta(points, home=_HOME_REFERENCE, now=now) is None


def test_resolve_commuting_eta_arrived_within_home_radius():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    points = [{"ts": now, "lat": 1.3, "lon": 103.8}]

    result = resolve_commuting_eta(points, home=_HOME_REFERENCE, now=now)

    assert result is not None
    assert result.arrived is True
    assert result.eta_seconds is None
    assert result.eta_at is None


def test_resolve_commuting_eta_single_fresh_point_is_ambiguous():
    """One fresh point far from home can't derive a closing speed -- leave untouched."""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    points = [{"ts": now, "lat": 1.5, "lon": 104.0}]
    assert resolve_commuting_eta(points, home=_HOME_REFERENCE, now=now) is None


def test_resolve_commuting_eta_not_closing_returns_none():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    earliest = now - timedelta(minutes=10)
    points = [
        {"ts": earliest, "lat": 1.32, "lon": 103.82},
        {"ts": now, "lat": 1.35, "lon": 103.85},  # farther from home than before
    ]
    assert resolve_commuting_eta(points, home=_HOME_REFERENCE, now=now) is None


def test_resolve_commuting_eta_computes_eta_when_closing():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    earliest = now - timedelta(minutes=10)
    points = [
        {"ts": earliest, "lat": 1.35, "lon": 103.85},
        {"ts": now, "lat": 1.32, "lon": 103.82},
    ]

    distance_earliest = haversine_meters(1.35, 103.85, _HOME_REFERENCE.lat, _HOME_REFERENCE.lon)
    distance_latest = haversine_meters(1.32, 103.82, _HOME_REFERENCE.lat, _HOME_REFERENCE.lon)
    expected_speed_mps = (distance_earliest - distance_latest) / 600
    expected_eta_seconds = distance_latest / expected_speed_mps

    result = resolve_commuting_eta(points, home=_HOME_REFERENCE, now=now)

    assert result is not None
    assert result.arrived is False
    assert result.distance_meters == pytest.approx(distance_latest)
    assert result.eta_seconds == pytest.approx(expected_eta_seconds)
    assert result.eta_at == now + timedelta(seconds=expected_eta_seconds)


def _calendar_pool(now: datetime) -> MagicMock:
    """One connection and clock, with real async context-manager boundaries."""
    pool = MagicMock()
    pool.fetch = AsyncMock()
    pool.fetchval = AsyncMock(return_value=now)
    pool.fetchrow = AsyncMock(
        return_value={
            "observed_at": now,
            "projection_schema": "public",
            "producer_role": "test-migration",
        }
    )
    pool.execute = AsyncMock()

    @asynccontextmanager
    async def scope():
        yield pool

    pool.acquire.side_effect = scope
    pool.transaction.side_effect = scope
    return pool


def _active_calendar_row(
    *,
    title: str,
    starts_at: datetime,
    ends_at: datetime,
    timezone: str = "UTC",
    all_day: bool = False,
    metadata: object = None,
) -> dict[str, object]:
    return {
        "title": title,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "timezone": timezone,
        "all_day": all_day,
        "metadata": metadata,
    }


# REQ-context-bus-004, REQ-context-bus-005, REQ-context-bus-006: existing behavioral controls below; SQL credit is separate.
async def test_calendar_context_producer_skips_explicit_butler_generated_event_but_keeps_human_event():
    now = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    pool = _calendar_pool(datetime(2026, 7, 1, 9, 0, tzinfo=UTC))
    pool.fetch = AsyncMock(
        return_value=[
            _active_calendar_row(
                title="BUTLER: Draft follow-up",
                starts_at=now - timedelta(minutes=5),
                ends_at=now + timedelta(minutes=30),
                metadata={"butler_generated": True},
            ),
            _active_calendar_row(
                title="Human standup",
                starts_at=now - timedelta(minutes=10),
                ends_at=now + timedelta(minutes=20),
                metadata={"butler_generated": False},
            ),
        ]
    )
    set_context_mock = AsyncMock()
    clear_context_mock = AsyncMock()

    with (
        patch("butlers.jobs.context_producers.set_context", new=set_context_mock),
        patch("butlers.jobs.context_producers.clear_context", new=clear_context_mock),
    ):
        result = await run_calendar_context_producer(pool)

    assert result["signal"] == "meeting"
    assert result["value"] == "Human standup"
    assert set_context_mock.await_args.kwargs["expires_at"] == now + timedelta(minutes=20)
    pool.fetch.assert_awaited_once()

    # REQ-context-bus-006: a visible but declined/free event is not a meeting.
    for metadata in (
        {"attendees": [{"self": True, "response_status": "declined"}]},
        {"attendees": [{"self": True, "responseStatus": "declined"}]},
        {"transparency": "transparent"},
    ):
        pool.fetch.return_value = [
            _active_calendar_row(
                title="Not attending",
                starts_at=now - timedelta(minutes=5),
                ends_at=now + timedelta(minutes=30),
                metadata=metadata,
            )
        ]
        set_context_mock.reset_mock()
        with (
            patch("butlers.jobs.context_producers.set_context", new=set_context_mock),
            patch("butlers.jobs.context_producers.clear_context", new=clear_context_mock),
        ):
            absent = await run_calendar_context_producer(pool)
        assert absent["signal"] is None
        set_context_mock.assert_not_awaited()

    # Full typed/attendance matrix stays within the existing registered proof species.
    async def observe(rows):
        pool.fetch.return_value = rows
        set_context_mock.reset_mock()
        clear_context_mock.reset_mock()
        with (
            patch("butlers.jobs.context_producers.set_context", new=set_context_mock),
            patch("butlers.jobs.context_producers.clear_context", new=clear_context_mock),
        ):
            result = await run_calendar_context_producer(pool)
        return result, [c.kwargs for c in set_context_mock.await_args_list]

    def event(kind="default", **changes):
        return {
            **_active_calendar_row(
                title="Plain title",
                starts_at=now - timedelta(minutes=5),
                ends_at=now + timedelta(minutes=30),
            ),
            "id": "b",
            "source_id": "source",
            "event_type": kind,
            "working_location": None,
            **changes,
        }

    for metadata in (
        {"attendees": [{"self": True, "response_status": "accepted"}], "transparency": "opaque"},
        {"attendees": [{"self": True, "responseStatus": "tentative"}]},
        {"attendees": [{"self": True, "responseStatus": "needsAction"}]},
        {"attendees": [{"response_status": "declined"}]},
        None,
        "bad-json",
    ):
        actual, writes = await observe([event(metadata=metadata)])
        assert actual["signal"] == "meeting" and len(writes) == 1
    # Ineligible newer rows do not hide a real earlier meeting.
    actual, _ = await observe(
        [
            event(metadata={"transparency": "transparent"}),
            event(title="Attended", starts_at=now - timedelta(minutes=10), id="a"),
        ]
    )
    assert actual["value"] == "Attended"
    actual, writes = await observe(
        [
            event(
                "outOfOffice", starts_at=now - timedelta(days=2), ends_at=now + timedelta(days=40)
            ),
            event(title="Later meeting"),
        ]
    )
    assert actual["signal"] == "away" and actual["value"] == "out of office"
    assert actual["cleared"] == ["meeting", "focused"]
    assert writes[0]["expires_at"] == now + timedelta(days=30)
    assert writes[0]["_observed_at"] == now
    actual, writes = await observe([event("focusTime")])
    assert actual["signal"] == "focused"
    assert actual["value"] == "focus time"
    assert "title" not in writes[0]["metadata"]
    for kind in ("default", "futureProviderType"):
        actual, writes = await observe([event(kind, title="Deep Work")])
        assert actual["signal"] == "focused"
        assert actual["value"] == "Deep Work"
        assert writes[0]["metadata"]["title"] == "Deep Work"
    for kind in ("outOfOffice", "focusTime"):
        actual, writes = await observe([event(kind, all_day=True)])
        assert actual["signal"] is None and writes == []
    for kind in ("outOfOffice", "focusTime", "workingLocation"):
        actual, writes = await observe(
            [
                event(
                    kind,
                    metadata={"butler_generated": True},
                    working_location={"type": "homeOffice"},
                )
            ]
        )
        assert actual["signal"] is None and writes == []
    # Location is independent, transparent-compatible, minimized and day-bounded.
    for location, expected in (
        ({"type": "homeOffice"}, "home office"),
        ({"type": "officeLocation", "label": "HQ"}, "HQ"),
        ({"type": "customLocation", "label": "Remote"}, "Remote"),
    ):
        actual, writes = await observe(
            [
                event(
                    "workingLocation",
                    timezone="UTC",
                    working_location=location,
                    metadata={"transparency": "transparent"},
                ),
                event("focusTime", id="a"),
            ]
        )
        assert actual["signal"] == "focused" and actual["working_location"] == expected
        assert {w["signal_type"] for w in writes} == {"focused", "working_location"}
    for changes in (
        {"timezone": "invalid"},
        {"working_location": {"type": "officeLocation"}},
        {"metadata": {"attendees": [{"self": True, "response_status": "declined"}]}},
    ):
        actual, writes = await observe(
            [event("workingLocation", **{"working_location": {"type": "homeOffice"}, **changes})]
        )
        assert actual["signal"] is None and writes == []
    # Exact local midnight plus DST follows the local day, not a fixed UTC day.
    from butlers.jobs.context_producers import _working_location_expiry

    for start, end in (
        (
            datetime(2026, 3, 8, tzinfo=ZoneInfo("America/New_York")),
            datetime(2026, 3, 9, tzinfo=ZoneInfo("America/New_York")),
        ),
        (
            datetime(2026, 11, 1, tzinfo=ZoneInfo("America/New_York")),
            datetime(2026, 11, 2, tzinfo=ZoneInfo("America/New_York")),
        ),
    ):
        row = event(
            "workingLocation",
            all_day=True,
            timezone="America/New_York",
            starts_at=start,
            ends_at=end,
            working_location={"type": "homeOffice"},
        )
        observed = start + timedelta(hours=1)
        assert _working_location_expiry(row, observed) == min(end, observed + timedelta(hours=24))
        assert (
            _working_location_expiry({**row, "ends_at": end + timedelta(days=1)}, observed) is None
        )
        assert _working_location_expiry(row, end) is None
    # Stable tie-breaking is independent of fetched row ordering.
    a, b = event(title="a", id="a"), event(title="b", id="b")
    assert (await observe([b, a]))[0]["value"] == "a"
    assert (await observe([a, b]))[0]["value"] == "a"
    # Runtime-owned General reads are qualified; unavailable General cannot
    # silently resolve a public projection through search_path.
    pool.fetchrow.return_value = {
        "observed_at": now,
        "projection_schema": "general",
        "producer_role": "butler_general_rw",
    }
    assert (await observe([a]))[0]["signal"] == "meeting"
    assert 'FROM "general".calendar_events' in pool.fetch.await_args.args[0]
    for schema, role in (("public", "butler_general_rw"), ("relationship", "test-migration")):
        pool.fetchrow.return_value = {
            "observed_at": now,
            "projection_schema": schema,
            "producer_role": role,
        }
        pool.fetch.reset_mock()
        set_context_mock.reset_mock()
        clear_context_mock.reset_mock()
        with (
            patch("butlers.jobs.context_producers.set_context", new=set_context_mock),
            patch("butlers.jobs.context_producers.clear_context", new=clear_context_mock),
            pytest.raises(RuntimeError, match="projection namespace unavailable"),
        ):
            await run_calendar_context_producer(pool)
        pool.fetch.assert_not_awaited()
        set_context_mock.assert_not_awaited()
        clear_context_mock.assert_not_awaited()
    pool.fetchrow.return_value = {
        "observed_at": now,
        "projection_schema": "public",
        "producer_role": "test-migration",
    }
    # Failure is propagated rather than converted into an absence transition.
    pool.fetch.side_effect = RuntimeError("synthetic unavailable projection")
    set_context_mock.reset_mock()
    clear_context_mock.reset_mock()
    with (
        patch("butlers.jobs.context_producers.set_context", new=set_context_mock),
        patch("butlers.jobs.context_producers.clear_context", new=clear_context_mock),
        pytest.raises(RuntimeError, match="synthetic unavailable projection"),
    ):
        await run_calendar_context_producer(pool)
    set_context_mock.assert_not_awaited()
    clear_context_mock.assert_not_awaited()


async def test_calendar_context_producer_treats_legacy_midnight_block_as_non_meeting():
    singapore = ZoneInfo("Asia/Singapore")
    starts_at = datetime(2026, 7, 1, 0, 0, tzinfo=singapore)
    pool = _calendar_pool(datetime(2026, 7, 1, 9, 0, tzinfo=UTC))
    pool.fetch = AsyncMock(
        return_value=[
            _active_calendar_row(
                title="Legacy all-day import",
                starts_at=starts_at,
                ends_at=starts_at + timedelta(days=1),
                timezone="Asia/Singapore",
                all_day=False,
                metadata={},
            )
        ]
    )
    set_context_mock = AsyncMock()
    clear_context_mock = AsyncMock()

    with (
        patch("butlers.jobs.context_producers.set_context", new=set_context_mock),
        patch("butlers.jobs.context_producers.clear_context", new=clear_context_mock),
    ):
        result = await run_calendar_context_producer(pool)

    assert result == {"signal": None, "cleared": ["meeting", "focused"]}
    set_context_mock.assert_not_awaited()
    assert clear_context_mock.await_count == 2


async def test_calendar_context_producer_malformed_provenance_retains_timed_human_event():
    now = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    pool = _calendar_pool(datetime(2026, 7, 1, 9, 0, tzinfo=UTC))
    pool.fetch = AsyncMock(
        return_value=[
            _active_calendar_row(
                title="Planning",
                starts_at=now - timedelta(minutes=5),
                ends_at=now + timedelta(minutes=25),
                timezone="not/a-timezone",
                metadata="not-a-json-object",
            )
        ]
    )
    set_context_mock = AsyncMock()
    clear_context_mock = AsyncMock()

    with (
        patch("butlers.jobs.context_producers.set_context", new=set_context_mock),
        patch("butlers.jobs.context_producers.clear_context", new=clear_context_mock),
    ):
        result = await run_calendar_context_producer(pool)

    assert result["signal"] == "meeting"
    assert result["value"] == "Planning"
    set_context_mock.assert_awaited_once()


async def test_travel_producer_publishes_trip_active_once_per_trip():
    """bu-317s5 slice 2: an active trip best-effort publishes travel.trip_active,
    memoized on the trip id via publish_domain_event_once (not on every tick)."""
    trip_id = uuid.uuid4()
    row = {
        "id": trip_id,
        "name": "Work trip",
        "destination": "Tokyo",
        "start_date": date(2026, 7, 20),
        "end_date": date(2026, 7, 25),
        "status": "active",
    }
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=row)
    set_context_mock = AsyncMock()
    publish_once_mock = AsyncMock(return_value={"status": "ok", "event_id": "e1", "deliveries": []})

    with (
        patch("butlers.jobs.context_producers.set_context", new=set_context_mock),
        patch(
            "butlers.core_tools._domain_events.publish_domain_event_once",
            new=publish_once_mock,
        ),
    ):
        result = await run_travel_context_producer(pool)

    assert result == {"signal": "traveling", "value": "Tokyo"}
    publish_once_mock.assert_awaited_once()
    kwargs = publish_once_mock.await_args.kwargs
    assert kwargs["event_type"] == "travel.trip_active"
    assert kwargs["source_butler"] == "travel"
    assert kwargs["dedup_namespace"] == "travel.trip_active"
    assert kwargs["dedup_key"] == str(trip_id)
    assert kwargs["payload"] == {
        "trip_id": str(trip_id),
        "name": "Work trip",
        "destination": "Tokyo",
        "start_date": "2026-07-20",
        "end_date": "2026-07-25",
        "status": "active",
    }


async def test_travel_producer_swallows_publish_failure():
    """A domain-event-bus hiccup must never break the context-bus signal write."""
    row = {
        "id": uuid.uuid4(),
        "name": "Work trip",
        "destination": "Tokyo",
        "start_date": date(2026, 7, 20),
        "end_date": date(2026, 7, 25),
        "status": "active",
    }
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=row)
    set_context_mock = AsyncMock()

    with (
        patch("butlers.jobs.context_producers.set_context", new=set_context_mock),
        patch(
            "butlers.core_tools._domain_events.publish_domain_event_once",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        result = await run_travel_context_producer(pool)

    assert result == {"signal": "traveling", "value": "Tokyo"}
    set_context_mock.assert_awaited_once()


async def test_travel_producer_no_active_trip_does_not_publish():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    clear_context_mock = AsyncMock()
    publish_once_mock = AsyncMock()

    with (
        patch("butlers.jobs.context_producers.clear_context", new=clear_context_mock),
        patch(
            "butlers.core_tools._domain_events.publish_domain_event_once",
            new=publish_once_mock,
        ),
    ):
        result = await run_travel_context_producer(pool)

    assert result == {"signal": None, "cleared": ["traveling"]}
    publish_once_mock.assert_not_awaited()


async def test_commuting_eta_producer_clears_when_at_home():
    pool = MagicMock()
    is_user_in_context_mock = AsyncMock(return_value=True)
    clear_context_mock = AsyncMock()

    with (
        patch("butlers.jobs.context_producers.is_user_in_context", new=is_user_in_context_mock),
        patch("butlers.jobs.context_producers.clear_context", new=clear_context_mock),
    ):
        result = await run_commuting_eta_context_producer(pool)

    assert result == {"signal": None, "reason": "at_home", "cleared": ["commuting"]}
    clear_context_mock.assert_awaited_once_with(pool, "travel", ContextSignal.commuting.value)
    pool.fetch.assert_not_called()


async def test_commuting_eta_producer_reports_unconfigured_without_home_reference(monkeypatch):
    monkeypatch.delenv("OWNTRACKS_PLACE_REFERENCES", raising=False)
    pool = MagicMock()
    is_user_in_context_mock = AsyncMock(return_value=False)

    with patch("butlers.jobs.context_producers.is_user_in_context", new=is_user_in_context_mock):
        result = await run_commuting_eta_context_producer(pool)

    assert result == {"signal": None, "reason": "unconfigured"}
    pool.fetch.assert_not_called()


async def test_commuting_eta_producer_treats_malformed_env_as_unconfigured(monkeypatch):
    monkeypatch.setenv("OWNTRACKS_PLACE_REFERENCES", "{not valid json")
    pool = MagicMock()
    is_user_in_context_mock = AsyncMock(return_value=False)

    with patch("butlers.jobs.context_producers.is_user_in_context", new=is_user_in_context_mock):
        result = await run_commuting_eta_context_producer(pool)

    assert result == {"signal": None, "reason": "unconfigured"}


async def test_commuting_eta_producer_reports_unmeasurable_without_fresh_points(monkeypatch):
    monkeypatch.setenv(
        "OWNTRACKS_PLACE_REFERENCES", json.dumps([{"label": "home", "lat": 1.3, "lon": 103.8}])
    )
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    is_user_in_context_mock = AsyncMock(return_value=False)
    set_context_mock = AsyncMock()
    clear_context_mock = AsyncMock()

    with (
        patch("butlers.jobs.context_producers.is_user_in_context", new=is_user_in_context_mock),
        patch("butlers.jobs.context_producers.set_context", new=set_context_mock),
        patch("butlers.jobs.context_producers.clear_context", new=clear_context_mock),
    ):
        result = await run_commuting_eta_context_producer(pool)

    assert result == {"signal": None, "reason": "unmeasurable"}
    set_context_mock.assert_not_awaited()
    clear_context_mock.assert_not_awaited()


async def test_commuting_eta_producer_clears_on_arrival(monkeypatch):
    monkeypatch.setenv(
        "OWNTRACKS_PLACE_REFERENCES",
        json.dumps([{"label": "home", "lat": 1.3, "lon": 103.8, "radius_m": 150.0}]),
    )
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"ts": now, "lat": 1.3, "lon": 103.8}])
    is_user_in_context_mock = AsyncMock(return_value=False)
    clear_context_mock = AsyncMock()

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    with (
        patch("butlers.jobs.context_producers.is_user_in_context", new=is_user_in_context_mock),
        patch("butlers.jobs.context_producers.clear_context", new=clear_context_mock),
        patch("butlers.jobs.context_producers.datetime", FrozenDatetime),
    ):
        result = await run_commuting_eta_context_producer(pool)

    assert result == {"signal": None, "reason": "arrived", "cleared": ["commuting"]}
    clear_context_mock.assert_awaited_once_with(pool, "travel", ContextSignal.commuting.value)


async def test_commuting_eta_producer_sets_signal_with_eta(monkeypatch):
    """Label matching is case-insensitive against the configured home reference."""
    monkeypatch.setenv(
        "OWNTRACKS_PLACE_REFERENCES",
        json.dumps([{"label": "Home", "lat": 1.3, "lon": 103.8, "radius_m": 150.0}]),
    )
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    earliest = now - timedelta(minutes=10)
    pool = MagicMock()
    pool.fetch = AsyncMock(
        return_value=[
            {"ts": earliest, "lat": 1.35, "lon": 103.85},
            {"ts": now, "lat": 1.32, "lon": 103.82},
        ]
    )
    is_user_in_context_mock = AsyncMock(return_value=False)
    set_context_mock = AsyncMock()

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    with (
        patch("butlers.jobs.context_producers.is_user_in_context", new=is_user_in_context_mock),
        patch("butlers.jobs.context_producers.set_context", new=set_context_mock),
        patch("butlers.jobs.context_producers.datetime", FrozenDatetime),
    ):
        result = await run_commuting_eta_context_producer(pool)

    assert result["signal"] == "commuting"
    kwargs = set_context_mock.await_args.kwargs
    assert kwargs["butler_name"] == "travel"
    assert kwargs["signal_type"] == ContextSignal.commuting.value
    assert kwargs["confidence"] == 0.6
    assert kwargs["expires_at"] > now
    assert kwargs["metadata"]["source"] == "owntracks"
    assert kwargs["metadata"]["distance_meters"] == result["distance_meters"]


async def test_sleep_producer_uses_shared_exact_policy_anchor():
    """Sleep expiry is the shared end-exclusive policy anchor, never end + 1h."""
    now = datetime(2026, 1, 1, 23, 30, tzinfo=UTC)
    policy = {"quiet_start_hour": 22, "quiet_end_hour": 7, "timezone": "UTC"}
    set_context_mock = AsyncMock()

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is UTC
            return now

    with (
        patch(
            "butlers.jobs.context_producers.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=policy),
        ),
        patch("butlers.jobs.context_producers.set_context", new=set_context_mock),
        patch("butlers.jobs.context_producers.datetime", FrozenDatetime),
    ):
        result = await run_sleep_window_context_producer(AsyncMock())

    expected = datetime(2026, 1, 2, 7, 0, tzinfo=UTC)
    assert result == {"signal": "sleeping", "expires_at": expected.isoformat()}
    assert set_context_mock.await_args.kwargs["expires_at"] == expected


# ---------------------------------------------------------------------------
# Integration tests (real Postgres via testcontainers)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def core_db_url(postgres_container) -> str:
    """Core-chain DB: calendar_events, approvals_policy, user_context all land in public."""
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture(scope="module")
def general_db_url(postgres_container) -> str:
    """Canonical General-owning projection, with the real managed bootstrap."""
    return create_migrated_test_db(
        postgres_container, migration_db_name(), chains=["core"], schemas={"core": "general"}
    )


def _replay_calendar_prep_bootstrap_acl(postgres_container, db_url):
    """Replay only the existing adopted ACL installers with trusted bootstrap.

    The normal fixture login does not own bootstrap-created schemas. Their
    best-effort GRANT clauses may therefore be skipped during ordinary replay.
    This fixture species provisions the already-adopted scheduled-read contract;
    it does not give the runtime handler a bootstrap connection or a new grant.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    db_name = urlparse(db_url).path.removeprefix("/")
    engine = create_engine(migration_bootstrap_db_url(postgres_container, db_name))
    try:
        with engine.begin() as conn:
            operations = Operations(MigrationContext.configure(conn))
            for filename in (
                "core_077_relationship_switchboard_read_grants.py",
                "core_143_email_butlers_switchboard_read_grants.py",
            ):
                path = Path(__file__).resolve().parents[2] / "alembic/versions/core" / filename
                spec = importlib.util.spec_from_file_location("calendar_prep_existing_acl", path)
                assert spec is not None and spec.loader is not None
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                with patch.object(module, "op", operations):
                    module.upgrade()
    finally:
        engine.dispose()


async def _calendar_role_pool(url: str, butler: str) -> asyncpg.Pool:
    assert butler in {"general", "switchboard", "relationship", "messenger", "travel"}

    async def setup(conn):
        await conn.execute(f'SET ROLE "butler_{butler}_rw"')
        await conn.execute(f'SET search_path TO "{butler}", public')

    return await asyncpg.create_pool(
        url, min_size=1, max_size=3, init=register_jsonb_codec, setup=setup
    )


async def _calendar_lock_boundary_control(owned, reader, handler, source_id):
    """Reach a real lock wait, cross an event boundary, then read durable state."""
    task = None
    await owned.execute("UPDATE calendar_events SET status='cancelled'")
    await _clear_non_dnd_context(owned)
    # Plant the sibling so its clear has a positive, durable timestamp witness.
    from butlers.context_bus import set_context

    await set_context(owned, "general", "focused", value="Synthetic previous focus")
    try:
        async with owned.acquire() as holder:
            async with holder.transaction():
                await holder.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended('calendar-context:general',0))"
                )
                task = asyncio.create_task(handler(owned, None))
                async with asyncio.timeout(15):
                    while not await holder.fetchval(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM pg_locks
                            WHERE locktype='advisory' AND NOT granted
                              AND database=(SELECT oid FROM pg_database WHERE datname=current_database())
                              AND classid::bigint=((hashtextextended('calendar-context:general',0)>>32)&4294967295)
                              AND objid::bigint=(hashtextextended('calendar-context:general',0)&4294967295)
                              AND objsubid=1
                        )
                        """
                    ):
                        if task.done():
                            await task
                            raise AssertionError(
                                "producer completed without the required lock wait"
                            )
                        await asyncio.sleep(0.01)
                boundary = await holder.fetchval("SELECT clock_timestamp()")
                await holder.executemany(
                    """
                    INSERT INTO calendar_events
                        (source_id,source_butler,origin_ref,title,timezone,starts_at,ends_at)
                    VALUES($1,'general',$2,$3,'UTC',$4,$5)
                    """,
                    [
                        (
                            source_id,
                            str(uuid.uuid4()),
                            "Expired while waiting",
                            boundary - timedelta(minutes=1),
                            boundary,
                        ),
                        (
                            source_id,
                            str(uuid.uuid4()),
                            "Current after waiting",
                            boundary,
                            boundary + timedelta(hours=1),
                        ),
                    ],
                )
            # Commit releases A's lock; B now observes both rows at READ COMMITTED.
        result = await asyncio.wait_for(task, timeout=15)
        current = await reader.fetchrow(
            "SELECT * FROM public.user_context WHERE signal_type='meeting' AND set_by_butler='general' AND superseded_at IS NULL"
        )
        sibling = await reader.fetchrow(
            "SELECT superseded_at FROM public.user_context WHERE signal_type='focused' AND set_by_butler='general'"
        )
        assert current is not None and sibling is not None
        assert current["value"] == result["value"]
        assert sibling["superseded_at"] == current["set_at"]
        return boundary, current
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _neutralized_calendar_transaction_clock(tmp_path):
    """Complete current source with only the old transaction-clock query restored.

    Generated experiment identity is outside production src so historical code
    cannot contaminate the current measured-source population.
    """
    import butlers.jobs.context_producers as producer_module

    source = Path(producer_module.__file__).read_text()
    current = "SELECT clock_timestamp() AS observed_at"
    assert source.count(current) == 1
    path = tmp_path / "historical_transaction_clock_control.py"
    path.write_text(source.replace(current, "SELECT now() AS observed_at"))
    spec = importlib.util.spec_from_file_location("calendar_old_transaction_clock_control", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # The complete module contains dataclasses, whose annotation resolution
    # requires their defining module to be registered during execution.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module.run_calendar_context_producer


async def _project_synthetic_calendar(pool, source_id, payload):
    """Actual Google normalizer and mounted writer; payload provenance is synthetic."""
    from types import SimpleNamespace

    from butlers.modules.calendar import CalendarModule, _google_event_to_calendar_event

    module = CalendarModule()
    module._butler_name = "general"
    module._db = SimpleNamespace(pool=pool)
    event = _google_event_to_calendar_event(payload, fallback_timezone="UTC")
    assert event is not None
    await module._project_provider_changes(
        source_id=source_id,
        provider_name="google",
        calendar_id="primary",
        updated_events=[event],
        cancelled_ids=[],
    )
    # An independent acquisition witnesses real column and row persistence.
    row = await pool.fetchrow(
        "SELECT * FROM calendar_events WHERE source_id=$1 AND origin_ref=$2",
        source_id,
        payload["id"],
    )
    assert row["event_type"] == event.event_type
    assert row["working_location"] == event.working_location
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM calendar_event_instances WHERE event_id=$1", row["id"]
        )
        == 1
    )
    return row


@pytest.fixture(scope="module")
def travel_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container, migration_db_name(), chains=["core", "travel"]
    )


@pytest.fixture(scope="module")
def home_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core", "home"])


async def _pool(url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(url, min_size=1, max_size=5, init=register_jsonb_codec)
    await _clear_non_dnd_context(p)
    return p


async def _clear_non_dnd_context(pool: asyncpg.Pool) -> None:
    """Reset fixture-owned context without bypassing the DND privilege boundary."""
    await pool.execute(
        """
        UPDATE public.user_context
        SET superseded_at = now()
        WHERE signal_type <> 'dnd' AND superseded_at IS NULL
        """
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not docker_available, reason="Docker not available")
class TestContextProducersIntegration:
    async def test_calendar_producer_sets_meeting_then_clears(
        self, core_db_url, general_db_url, tmp_path
    ):
        pool = await _pool(core_db_url)
        try:
            await pool.execute("TRUNCATE calendar_events, calendar_sources CASCADE")
            source_id = await pool.fetchval(
                "INSERT INTO calendar_sources (source_key, source_kind) "
                "VALUES ('test-src', 'provider') RETURNING id"
            )
            # An event happening right now.
            await pool.execute(
                """
                INSERT INTO calendar_events
                    (source_id, source_butler, origin_ref, title, timezone,
                     starts_at, ends_at, status)
                VALUES ($1, 'general', 'e1', 'Standup', 'UTC',
                        now() - interval '5 minutes', now() + interval '25 minutes', 'confirmed')
                """,
                source_id,
            )
            result = await run_calendar_context_producer(pool)
            assert result["signal"] == "meeting"
            row = await pool.fetchrow(
                "SELECT value, expires_at FROM public.user_context "
                "WHERE signal_type = 'meeting' AND set_by_butler = 'general' "
                "AND superseded_at IS NULL"
            )
            assert row is not None and row["value"] == "Standup"

            # Event ends: producer clears meeting.
            await pool.execute("UPDATE calendar_events SET ends_at = now() - interval '1 minute'")
            result2 = await run_calendar_context_producer(pool)
            assert result2["signal"] is None
            active = await pool.fetchval(
                "SELECT count(*) FROM public.user_context "
                "WHERE signal_type IN ('meeting', 'focused') AND superseded_at IS NULL "
                "AND expires_at > now()"
            )
            assert active == 0
        finally:
            await pool.close()

        # REQ-context-bus-006 / REQ-module-calendar-002: owning General, not public-only diagnostic.
        from butlers.context_bus import format_context_preamble, get_active_context, set_context
        from butlers.scheduled_jobs import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY
        from butlers.tools.switchboard.insight.broker import get_suppressing_context_signal

        handler = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY["general"]["context_producer_calendar"]
        owned = await _calendar_role_pool(general_db_url, "general")
        reader = await _calendar_role_pool(general_db_url, "switchboard")
        try:
            assert await owned.fetchval("SELECT current_user") == "butler_general_rw"
            assert await owned.fetchval("SELECT current_schema()") == "general"
            assert (
                await owned.fetchval(
                    "SELECT rolcreaterole FROM pg_roles WHERE rolname=current_user"
                )
                is False
            )
            await _clear_non_dnd_context(owned)
            sid = await owned.fetchval(
                "INSERT INTO calendar_sources(source_key,source_kind,provider,calendar_id) VALUES('typed-source','provider','google','primary') RETURNING id"
            )
            now = await owned.fetchval("SELECT now()")
            base = {
                "id": "typed-current",
                "summary": "Standup",
                "status": "confirmed",
                "start": {"dateTime": (now - timedelta(minutes=5)).isoformat()},
                "end": {"dateTime": (now + timedelta(minutes=25)).isoformat()},
                "attendees": [
                    {"self": True, "email": "synthetic@example.test", "responseStatus": "accepted"}
                ],
                "transparency": "opaque",
            }
            await _project_synthetic_calendar(owned, sid, base)
            assert (await handler(owned, None))["signal"] == "meeting"
            assert await get_suppressing_context_signal(reader, now=now) == "meeting"
            assert "Standup" in format_context_preamble(await get_active_context(reader))
            for changes in (
                {
                    "attendees": [
                        {
                            "self": True,
                            "email": "synthetic@example.test",
                            "responseStatus": "declined",
                        }
                    ]
                },
                {"transparency": "transparent"},
            ):
                await _project_synthetic_calendar(owned, sid, {**base, **changes})
                assert (await handler(owned, None))["signal"] is None
                assert await get_suppressing_context_signal(reader, now=now) is None
            # An eligible earlier overlap wins after a newer ineligible row is rejected.
            positive = {
                **base,
                "id": "older-attended",
                "summary": "Earlier meeting",
                "start": {"dateTime": (now - timedelta(minutes=10)).isoformat()},
            }
            await _project_synthetic_calendar(owned, sid, positive)
            assert (await handler(owned, None))["value"] == "Earlier meeting"
            ooo = {
                **base,
                "id": "ooo",
                "summary": "Private title",
                "eventType": "outOfOffice",
                "start": {"dateTime": (now - timedelta(days=2)).isoformat()},
                "end": {"dateTime": (now + timedelta(days=40)).isoformat()},
            }
            await _project_synthetic_calendar(owned, sid, ooo)
            assert (await handler(owned, None))["signal"] == "away"
            first = await reader.fetchrow(
                "SELECT * FROM public.user_context WHERE signal_type='away' AND set_by_butler='general'"
            )
            assert first["value"] == "out of office"
            assert first["expires_at"] - first["set_at"] == timedelta(days=30)
            assert first["metadata"]["event_type"] == "outOfOffice"
            assert await get_suppressing_context_signal(reader, now=now) is None
            await handler(owned, None)
            second = await reader.fetchrow(
                "SELECT * FROM public.user_context WHERE signal_type='away' AND set_by_butler='general'"
            )
            assert second["set_at"] >= first["set_at"]
            # A real later failing transition rolls back earlier set_context effects.
            await owned.execute(
                "UPDATE calendar_events SET ends_at=now()-interval '1 second' WHERE origin_ref='ooo'"
            )
            before = await reader.fetch(
                "SELECT * FROM public.user_context ORDER BY signal_type,set_by_butler"
            )
            with patch(
                "butlers.jobs.context_producers.clear_context",
                side_effect=RuntimeError("synthetic transition failure"),
            ):
                with pytest.raises(RuntimeError, match="synthetic transition failure"):
                    await handler(owned, None)
            assert (
                await reader.fetch(
                    "SELECT * FROM public.user_context ORDER BY signal_type,set_by_butler"
                )
                == before
            )
            assert (await handler(owned, None))["signal"] == "meeting"
            assert (
                await reader.fetchval(
                    "SELECT superseded_at IS NOT NULL FROM public.user_context WHERE signal_type='away' AND set_by_butler='general'"
                )
                is True
            )
            # Successful observed absence preserves an unrelated assertion in General's slot.
            await set_context(
                owned, "general", "away", value="manually declared", metadata={"source": "manual"}
            )
            await owned.execute("UPDATE calendar_events SET ends_at=now()-interval '1 second'")
            assert (await handler(owned, None))["signal"] is None
            assert (
                await reader.fetchval(
                    "SELECT value FROM public.user_context WHERE signal_type='away' AND superseded_at IS NULL"
                )
                == "manually declared"
            )
            await set_context(
                owned, "general", "dnd", value="synthetic explicit DND", mutation_id=uuid.uuid4()
            )
            dnd_before = await reader.fetchrow(
                "SELECT * FROM public.user_context WHERE signal_type='dnd' AND set_by_butler='general'"
            )
            await handler(owned, None)
            assert (
                await reader.fetchrow(
                    "SELECT * FROM public.user_context WHERE signal_type='dnd' AND set_by_butler='general'"
                )
                == dnd_before
            )
            assert await get_suppressing_context_signal(reader, now=now) == "dnd"
            travel_writer = await _calendar_role_pool(general_db_url, "travel")
            try:
                await set_context(travel_writer, "travel", "traveling", value="synthetic trip")
                travel_before = await reader.fetchrow(
                    "SELECT * FROM public.user_context WHERE signal_type='traveling' AND set_by_butler='travel'"
                )
                await handler(owned, None)
                assert (
                    await reader.fetchrow(
                        "SELECT * FROM public.user_context WHERE signal_type='traveling' AND set_by_butler='travel'"
                    )
                    == travel_before
                )
            finally:
                await travel_writer.close()
            # Projection unavailable is not empty: reversible test-owned DDL fault,
            # actual registered handler refusal and independent unchanged signal readback.
            before = await reader.fetch(
                "SELECT * FROM public.user_context ORDER BY signal_type,set_by_butler"
            )
            # The existing normal migration login owns the disposable projection;
            # the runtime role does not get DDL or any enlarged grant.
            admin = await asyncpg.connect(general_db_url)
            try:
                await admin.execute(
                    "ALTER TABLE general.calendar_events RENAME TO unavailable_calendar_events"
                )
                with pytest.raises(asyncpg.UndefinedTableError):
                    await handler(owned, None)
            finally:
                await admin.execute(
                    "ALTER TABLE general.unavailable_calendar_events RENAME TO calendar_events"
                )
                await admin.close()
            assert (
                await reader.fetch(
                    "SELECT * FROM public.user_context ORDER BY signal_type,set_by_butler"
                )
                == before
            )
            # Two actual acquisitions: B starts before A's event boundary and
            # must use the one post-lock wall clock for selection, set and clear.
            boundary, current = await _calendar_lock_boundary_control(owned, reader, handler, sid)
            assert current["value"] == "Current after waiting"
            assert current["set_at"] >= boundary
            assert current["expires_at"] == boundary + timedelta(hours=1)
            # Neutralizing only clock_timestamp -> now reaches the expired row:
            # a real PostgreSQL counterexample, independently read after commit.
            old_clock = _neutralized_calendar_transaction_clock(tmp_path)
            old_boundary, old = await _calendar_lock_boundary_control(owned, reader, old_clock, sid)
            assert old["value"] == "Expired while waiting"
            assert old["set_at"] < old_boundary
            with pytest.raises(AssertionError):
                assert old["value"] == "Current after waiting"
            boundary, restored = await _calendar_lock_boundary_control(owned, reader, handler, sid)
            assert restored["value"] == "Current after waiting"
            assert restored["set_at"] >= boundary
            # Genuine successful absence clears the planted meeting with the
            # same post-serialization observation, without touching DND.
            await owned.execute("UPDATE calendar_events SET status='cancelled'")
            assert (await handler(owned, None))["signal"] is None
            assert (
                await reader.fetchval(
                    "SELECT superseded_at IS NOT NULL FROM public.user_context WHERE signal_type='meeting' AND set_by_butler='general'"
                )
                is True
            )
            assert (
                await reader.fetchrow(
                    "SELECT * FROM public.user_context WHERE signal_type='dnd' AND set_by_butler='general'"
                )
                == dnd_before
            )
        finally:
            await owned.close()
            await reader.close()

    async def test_calendar_producer_classifies_focus_block(
        self, core_db_url, general_db_url, postgres_container, record_property
    ):
        pool = await _pool(core_db_url)
        try:
            await pool.execute("TRUNCATE calendar_events, calendar_sources CASCADE")
            source_id = await pool.fetchval(
                "INSERT INTO calendar_sources (source_key, source_kind) "
                "VALUES ('test-src2', 'provider') RETURNING id"
            )
            await pool.execute(
                """
                INSERT INTO calendar_events
                    (source_id, source_butler, origin_ref, title, timezone,
                     starts_at, ends_at, status)
                VALUES ($1, 'general', 'e2', 'Deep Work', 'UTC',
                        now() - interval '5 minutes', now() + interval '55 minutes', 'confirmed')
                """,
                source_id,
            )
            result = await run_calendar_context_producer(pool)
            assert result["signal"] == "focused"
        finally:
            await pool.close()

        from butlers.context_bus import format_context_preamble, get_active_context, set_context

        owned = await _calendar_role_pool(general_db_url, "general")
        reader = await _calendar_role_pool(general_db_url, "switchboard")
        try:
            await owned.execute("UPDATE calendar_events SET status='cancelled'")
            await _clear_non_dnd_context(owned)
            sid = await owned.fetchval(
                "INSERT INTO calendar_sources(source_key,source_kind) VALUES('focus-location-source','provider') RETURNING id"
            )
            now = await owned.fetchval("SELECT now()")
            base = {
                "id": "structural-focus",
                "summary": "Plain title",
                "status": "confirmed",
                "eventType": "focusTime",
                "start": {"dateTime": (now - timedelta(minutes=5)).isoformat()},
                "end": {"dateTime": (now + timedelta(minutes=25)).isoformat()},
            }
            await _project_synthetic_calendar(owned, sid, base)
            assert (await run_calendar_context_producer(owned))["signal"] == "focused"
            focused = await reader.fetchrow(
                "SELECT value,metadata FROM public.user_context WHERE signal_type='focused' AND set_by_butler='general' AND superseded_at IS NULL"
            )
            assert focused["value"] == "focus time"
            assert "title" not in focused["metadata"]
            assert "focus time" in format_context_preamble(await get_active_context(reader))
            assert "Plain title" not in format_context_preamble(await get_active_context(reader))
            for kind in ("default", "futureProviderType"):
                await _project_synthetic_calendar(
                    owned, sid, {**base, "eventType": kind, "summary": "Deep Work"}
                )
                assert (await run_calendar_context_producer(owned))["signal"] == "focused"
                ordinary_focus = await reader.fetchrow(
                    "SELECT value,metadata FROM public.user_context WHERE signal_type='focused' AND set_by_butler='general' AND superseded_at IS NULL"
                )
                assert ordinary_focus["value"] == "Deep Work"
                assert ordinary_focus["metadata"]["title"] == "Deep Work"
                assert "Deep Work" in format_context_preamble(await get_active_context(reader))
            await _project_synthetic_calendar(owned, sid, base)
            assert (await run_calendar_context_producer(owned))["value"] == "focus time"
            for kind, properties, value in (
                ("homeOffice", {"homeOffice": True}, "home office"),
                (
                    "officeLocation",
                    {"officeLocation": {"label": "HQ", "buildingId": "discard"}},
                    "HQ",
                ),
                ("customLocation", {"customLocation": {"label": "Remote"}}, "Remote"),
            ):
                payload = {
                    **base,
                    "id": "working-place",
                    "eventType": "workingLocation",
                    "transparency": "transparent",
                    "workingLocationProperties": {"type": kind, **properties},
                }
                await _project_synthetic_calendar(owned, sid, payload)
                actual = await run_calendar_context_producer(owned)
                assert actual["signal"] == "focused" and actual["working_location"] == value
                signals = await get_active_context(reader)
                assert {s.signal_type for s in signals} >= {"focused", "working_location"}
                assert value in format_context_preamble(signals)
            await _project_synthetic_calendar(
                owned, sid, {**payload, "workingLocationProperties": None}
            )
            assert "working_location" not in await run_calendar_context_producer(owned)
            assert (
                await reader.fetchval(
                    "SELECT superseded_at IS NOT NULL FROM public.user_context WHERE signal_type='working_location' AND set_by_butler='general'"
                )
                is True
            )
            before_location = await reader.fetchrow(
                "SELECT * FROM public.user_context WHERE signal_type='working_location' AND set_by_butler='general'"
            )
            with pytest.raises(PermissionError):
                await set_context(reader, "switchboard", "working_location", value="forbidden")
            assert (
                await reader.fetchrow(
                    "SELECT * FROM public.user_context WHERE signal_type='working_location' AND set_by_butler='general'"
                )
                == before_location
            )
            # Current local-day date-only declaration, independent persisted expiry readback.
            from zoneinfo import ZoneInfo

            zone = ZoneInfo("America/New_York")
            local = now.astimezone(zone)
            tomorrow = local.date() + timedelta(days=1)
            date_only = {
                **payload,
                "start": {"date": local.date().isoformat(), "timeZone": "America/New_York"},
                "end": {"date": tomorrow.isoformat(), "timeZone": "America/New_York"},
                "workingLocationProperties": {"type": "homeOffice"},
            }
            await _project_synthetic_calendar(owned, sid, date_only)
            assert (await run_calendar_context_producer(owned))["working_location"] == "home office"
            expiry = await reader.fetchval(
                "SELECT expires_at FROM public.user_context WHERE signal_type='working_location' AND set_by_butler='general'"
            )
            assert expiry == datetime.combine(tomorrow, datetime.min.time(), zone)
            await set_context(
                owned,
                "general",
                "working_location",
                value="manual place",
                metadata={"source": "manual"},
            )
            # A different source's future event makes the old broad expiry
            # statement deterministically invalid under the canonical CHECK.
            # Keep the failure in a savepoint and verify rollback after commit.
            other_sid = await owned.fetchval(
                "INSERT INTO calendar_sources(source_key,source_kind) VALUES('interval-positive-source','provider') RETURNING id"
            )
            future = await _project_synthetic_calendar(
                owned,
                other_sid,
                {
                    **base,
                    "id": "interval-validity-positive",
                    "summary": "Synthetic future ordinary event",
                    "eventType": "default",
                    "start": {"dateTime": (now + timedelta(days=2)).isoformat()},
                    "end": {"dateTime": (now + timedelta(days=2, hours=1)).isoformat()},
                },
            )
            windows_sql = (
                "SELECT id,source_id,starts_at,ends_at,status FROM calendar_events ORDER BY id"
            )
            before_windows = await owned.fetch(windows_sql)
            assert any(r["source_id"] == sid and r["status"] == "confirmed" for r in before_windows)
            assert any(
                r["id"] == future["id"] and r["status"] == "confirmed" for r in before_windows
            )
            async with owned.acquire() as conn:
                async with conn.transaction():
                    with pytest.raises(asyncpg.CheckViolationError) as rejected_expiry:
                        async with conn.transaction():
                            await conn.execute(
                                "UPDATE calendar_events SET ends_at=now()-interval '1 second'"
                            )
                    assert rejected_expiry.value.sqlstate == "23514"
                    assert rejected_expiry.value.constraint_name == "calendar_events_window_check"
            # This pool acquisition follows the outer transaction's commit.
            assert await owned.fetch(windows_sql) == before_windows
            # Prepare successful absence only for this fixture's source. Status
            # cancellation preserves every interval and the unrelated positive.
            await owned.execute(
                "UPDATE calendar_events SET status='cancelled' WHERE source_id=$1", sid
            )
            after_windows = await owned.fetch(windows_sql)
            assert [tuple(r)[:4] for r in after_windows] == [tuple(r)[:4] for r in before_windows]
            assert all(r["status"] == "cancelled" for r in after_windows if r["source_id"] == sid)
            assert [r for r in after_windows if r["source_id"] != sid] == [
                r for r in before_windows if r["source_id"] != sid
            ]
            await run_calendar_context_producer(owned)
            assert (
                await reader.fetchval(
                    "SELECT value FROM public.user_context WHERE signal_type='working_location' AND superseded_at IS NULL"
                )
                == "manual place"
            )
            # Real owning-role projection -> unchanged workspace fan-out ->
            # radar mapping: status hours vanish while opaque overlaps stay visible.
            from butlers.api.db import DatabaseManager
            from butlers.api.read_models.calendar_workspace_v1 import (
                query_calendar_conflicts,
                query_calendar_workspace,
            )

            db = DatabaseManager()
            db._pools["general"] = owned
            db._butler_schemas["general"] = "general"
            db._butler_modules["general"] = frozenset({"calendar"})
            day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            typed_rows = []
            for kind, title in (
                ("outOfOffice", "Synthetic opaque OOO"),
                ("workingLocation", "Synthetic opaque location"),
            ):
                typed_rows.append(
                    await _project_synthetic_calendar(
                        owned,
                        sid,
                        {
                            **base,
                            "id": "radar-" + kind,
                            "summary": title,
                            "eventType": kind,
                            "transparency": "opaque",
                            "start": {"dateTime": (day + timedelta(hours=1)).isoformat()},
                            "end": {"dateTime": (day + timedelta(hours=9)).isoformat()},
                            "workingLocationProperties": {"type": "homeOffice"},
                        },
                    )
                )
            workspace, failed = await query_calendar_workspace(
                db, view="user", start=day, end=day + timedelta(days=1)
            )
            assert failed == []
            selected = {
                r.event_id: r for r in workspace if r.event_id in {t["id"] for t in typed_rows}
            }
            assert {r.event_type for r in selected.values()} == {"outOfOffice", "workingLocation"}
            opaque_ids = {str(r.instance_id) for r in selected.values()}
            scan = await query_calendar_conflicts(
                db, start=day, end=day + timedelta(days=1), display_tz=ZoneInfo("UTC")
            )
            assert scan.available is True
            assert not any(i.kind == "overloaded_day" for i in scan.issues)
            assert any(
                i.kind == "overlap" and opaque_ids <= {e.entry_id for e in i.events}
                for i in scan.issues
            )
            await _project_synthetic_calendar(
                owned,
                sid,
                {
                    **base,
                    "id": "radar-future-ordinary",
                    "summary": "Synthetic unknown ordinary positive",
                    "eventType": "futureProviderType",
                    "start": {"dateTime": (day + timedelta(hours=1)).isoformat()},
                    "end": {"dateTime": (day + timedelta(hours=9)).isoformat()},
                },
            )
            positive = await query_calendar_conflicts(
                db, start=day, end=day + timedelta(days=1), display_tz=ZoneInfo("UTC")
            )
            assert positive.available is True
            assert any(i.kind == "overloaded_day" for i in positive.issues)
        finally:
            await owned.close()
            await reader.close()

        # Existing permitted cached prep topology: actual owning-schema selectors,
        # planted ordinary notes/commitment/thread positives, and stale status-key pruning.
        from butlers.core.state import state_get
        from butlers.jobs.calendar_prep import (
            prep_key,
            run_messenger_calendar_prep_contribution,
            run_relationship_calendar_prep_contribution,
            run_travel_calendar_prep_contribution,
        )
        from butlers.migrations import run_migrations

        for schema in ("relationship", "messenger", "travel", "switchboard"):
            await run_migrations(general_db_url, chain="core", schema=schema)
        await run_migrations(general_db_url, chain="memory", schema="relationship")
        await run_migrations(general_db_url, chain="switchboard", schema="switchboard")
        # Only disposable canonical fixture data; no new grants or source DDL.
        seed = await asyncpg.create_pool(
            general_db_url, min_size=1, max_size=1, init=register_jsonb_codec
        )
        entity = uuid.uuid4()
        try:
            await seed.execute(
                "INSERT INTO public.entities(id,canonical_name) VALUES($1,'Synthetic attendee')",
                entity,
            )
            await seed.execute(
                "INSERT INTO relationship.facts(subject,predicate,content,scope,entity_id) VALUES('Synthetic attendee','contact_note','Synthetic note','relationship',$1)",
                entity,
            )
            await seed.execute(
                "INSERT INTO public.owner_conditions(source,fingerprint,episode,summary,metadata) VALUES('relationship:commitment',$1,1,'Synthetic promise',$2)",
                str(uuid.uuid4()),
                {
                    "class": "commitment",
                    "kind": "promise",
                    "direction": "outbound",
                    "counterparty_entity_id": str(entity),
                },
            )
            await seed.execute(
                "SELECT switchboard.switchboard_message_inbox_ensure_partition(now())"
            )
            await seed.execute(
                "INSERT INTO switchboard.message_inbox(normalized_text,request_context) VALUES('Synthetic recent thread',$1)",
                {
                    "source_channel": "email",
                    "source_sender_entity_id": str(entity),
                    "source_thread_identity": "synthetic-thread",
                    "subject": "Synthetic subject",
                },
            )
            # Observe the ordinary migration replay's actual ACL state first.
            # A denied read must be a real schema/table privilege error; the
            # same planted thread is the positive witness after bootstrap replay.
            before_acl = {}
            for butler in ("relationship", "messenger", "travel"):
                probe = await _calendar_role_pool(general_db_url, butler)
                try:
                    readable = await probe.fetchval(
                        "SELECT has_schema_privilege(current_user,'switchboard','USAGE')"
                    )
                    before_acl[butler] = readable
                    if readable:
                        assert (
                            await probe.fetchval(
                                "SELECT normalized_text FROM switchboard.message_inbox WHERE normalized_text='Synthetic recent thread'"
                            )
                            == "Synthetic recent thread"
                        )
                    else:
                        with pytest.raises(asyncpg.InsufficientPrivilegeError) as denied:
                            await probe.fetchval(
                                "SELECT normalized_text FROM switchboard.message_inbox WHERE normalized_text='Synthetic recent thread'"
                            )
                        assert denied.value.sqlstate == "42501"
                finally:
                    await probe.close()
            record_property(
                "calendar_prep_pre_bootstrap_schema_usage", json.dumps(before_acl, sort_keys=True)
            )
            # Existing adopted bootstrap ACL authority is separate from the
            # normal migration login and every runtime SET ROLE below.
            await asyncio.to_thread(
                _replay_calendar_prep_bootstrap_acl, postgres_container, general_db_url
            )
            for butler, handler in (
                ("relationship", run_relationship_calendar_prep_contribution),
                ("messenger", run_messenger_calendar_prep_contribution),
                ("travel", run_travel_calendar_prep_contribution),
            ):
                role_pool = await _calendar_role_pool(general_db_url, butler)
                try:
                    assert await role_pool.fetchval("SELECT current_user") == f"butler_{butler}_rw"
                    assert (
                        await role_pool.fetchval(
                            "SELECT rolcreaterole FROM pg_roles WHERE rolname=current_user"
                        )
                        is False
                    )
                    assert (
                        await role_pool.fetchval(
                            "SELECT has_schema_privilege(current_user,'switchboard','USAGE')"
                        )
                        is True
                    )
                    assert (
                        await role_pool.fetchval(
                            "SELECT has_table_privilege(current_user,'switchboard.message_inbox','SELECT')"
                        )
                        is True
                    )
                    sid = await role_pool.fetchval(
                        "INSERT INTO calendar_sources(source_key,source_kind) VALUES('prep-source','provider') RETURNING id"
                    )
                    ordinary = await role_pool.fetchval(
                        "INSERT INTO calendar_events(source_id,source_butler,origin_ref,title,timezone,starts_at,ends_at) VALUES($1,$2,'ordinary','Synthetic meeting','UTC',now()+interval '1 hour',now()+interval '2 hours') RETURNING id",
                        sid,
                        butler,
                    )
                    await role_pool.execute(
                        "INSERT INTO calendar_event_entities(event_id,entity_id) VALUES($1,$2)",
                        ordinary,
                        entity,
                    )
                    for kind in ("outOfOffice", "workingLocation"):
                        status = await role_pool.fetchval(
                            "INSERT INTO calendar_events(source_id,source_butler,origin_ref,title,timezone,starts_at,ends_at,event_type) VALUES($1,$2,$3,'Synthetic status','UTC',now()+interval '1 hour',now()+interval '2 hours',$3) RETURNING id",
                            sid,
                            butler,
                            kind,
                        )
                        await role_pool.execute(
                            "INSERT INTO calendar_event_entities(event_id,entity_id) VALUES($1,$2)",
                            status,
                            entity,
                        )
                        await state_set(role_pool, prep_key(str(status)), {"old": True})
                        outcome = await handler(role_pool, None)
                        assert outcome["events_written"] == 1
                        assert await state_get(role_pool, prep_key(str(status))) is None
                        envelope = await state_get(role_pool, prep_key(str(ordinary)))
                        assert envelope["butler"] == butler and envelope["has_context"] is True
                        assert envelope["attendees"][0]["entity_id"] == str(entity)
                        if butler == "relationship":
                            assert envelope["attendees"][0]["notes"] == [
                                {"kind": "contact_note", "text": "Synthetic note"}
                            ]
                            assert (
                                envelope["attendees"][0]["commitments"][0]["summary"]
                                == "Synthetic promise"
                            )
                        else:
                            assert (
                                envelope["attendees"][0]["message_context"][0]["thread_id"]
                                == "synthetic-thread"
                            )
                finally:
                    await role_pool.close()
        finally:
            await seed.close()

    async def test_travel_producer_sets_traveling_then_clears(self, travel_db_url):
        pool = await _pool(travel_db_url)
        try:
            await pool.execute("TRUNCATE travel.trips CASCADE")
            await pool.execute(
                "TRUNCATE public.domain_events, public.domain_event_deliveries CASCADE"
            )
            await pool.execute("DELETE FROM public.state")
            trip_id = await pool.fetchval(
                """
                INSERT INTO travel.trips (name, destination, start_date, end_date, status)
                VALUES ('Work trip', 'Tokyo', current_date - 1, current_date + 2, 'active')
                RETURNING id
                """
            )
            result = await run_travel_context_producer(pool)
            assert result["signal"] == "traveling" and result["value"] == "Tokyo"
            row = await pool.fetchrow(
                "SELECT value FROM public.user_context "
                "WHERE signal_type = 'traveling' AND set_by_butler = 'travel' "
                "AND superseded_at IS NULL AND expires_at > now()"
            )
            assert row is not None and row["value"] == "Tokyo"

            # bu-317s5 slice 2: the same tick best-effort published
            # travel.trip_active exactly once, fanned out to Health (seeded,
            # core_189) -- even with no live switchboard_client, the event row
            # itself is durably recorded regardless of fan-out outcome.
            event_rows = await pool.fetch(
                "SELECT id, source_butler, payload FROM public.domain_events "
                "WHERE event_type = 'travel.trip_active'"
            )
            assert len(event_rows) == 1
            assert event_rows[0]["source_butler"] == "travel"
            payload = event_rows[0]["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            assert payload["trip_id"] == str(trip_id)

            # A second tick while the SAME trip is still active must not
            # re-publish (dedup memoized on the trip id via state).
            result_again = await run_travel_context_producer(pool)
            assert result_again["signal"] == "traveling"
            event_rows_again = await pool.fetch(
                "SELECT id FROM public.domain_events WHERE event_type = 'travel.trip_active'"
            )
            assert len(event_rows_again) == 1

            await pool.execute("UPDATE travel.trips SET status = 'completed'")
            result2 = await run_travel_context_producer(pool)
            assert result2["signal"] is None
            active = await pool.fetchval(
                "SELECT count(*) FROM public.user_context "
                "WHERE signal_type = 'traveling' AND superseded_at IS NULL AND expires_at > now()"
            )
            assert active == 0
        finally:
            await pool.close()

    async def _mark_ha_source_healthy(self, pool: asyncpg.Pool) -> None:
        await pool.execute(
            """
            INSERT INTO ha_source_health (source, status, last_success_at, updated_at)
            VALUES ('home_assistant', 'healthy', now(), now())
            ON CONFLICT (source) DO UPDATE SET
                status = 'healthy', last_success_at = now(), updated_at = now()
            """
        )

    async def _mark_ha_source_error(self, pool: asyncpg.Pool) -> None:
        await pool.execute(
            """
            INSERT INTO ha_source_health (source, status, last_error_at, last_error, updated_at)
            VALUES ('home_assistant', 'error', now(), 'simulated outage', now())
            ON CONFLICT (source) DO UPDATE SET
                status = 'error', last_error_at = now(), last_error = 'simulated outage',
                updated_at = now()
            """
        )

    async def test_home_presence_producer_sets_and_clears(self, home_db_url):
        pool = await _pool(home_db_url)
        try:
            await self._mark_ha_source_healthy(pool)
            await state_set(pool, "home:presence:owner_entities", ["person.owner"])

            # Build last_updated from the Python clock (the same clock the
            # producer reads via datetime.now(UTC)) rather than PG now(): under
            # libfaketime the Python process clock is shifted +45d/+120d but the
            # testcontainer Postgres clock is not, so a PG-now() row would look
            # ~45 days stale to the freshness gate and resolve to "unknown".
            # Python-relative timestamps stay fresh under both clocks, keeping
            # faketime coverage of the 30-min freshness gate.
            await pool.execute("TRUNCATE ha_entity_snapshot")
            # The reader guard (bu-8cdl1.12 slice 1, extended to this producer
            # by bu-8t4sc) requires a healthy ha_source_health row before it
            # will trust ha_entity_snapshot at all.
            await pool.execute(
                "INSERT INTO ha_source_health (source, status, last_success_at, updated_at) "
                "VALUES ('home_assistant', 'healthy', now(), now()) "
                "ON CONFLICT (source) DO UPDATE SET status = 'healthy', "
                "last_success_at = now(), updated_at = now()"
            )
            await pool.execute(
                "INSERT INTO ha_entity_snapshot (entity_id, state, last_updated) "
                "VALUES ('person.owner', 'home', $1)",
                datetime.now(UTC),
            )
            result = await run_home_presence_context_producer(pool)
            assert result["presence"] == "home"
            assert await pool.fetchval(
                "SELECT count(*) FROM public.user_context "
                "WHERE signal_type = 'at_home' AND set_by_butler = 'home' "
                "AND superseded_at IS NULL AND expires_at > now()"
            )

            # Owner leaves: producer clears at_home.
            await pool.execute(
                "UPDATE ha_entity_snapshot SET state = 'not_home', last_updated = $1",
                datetime.now(UTC),
            )
            result2 = await run_home_presence_context_producer(pool)
            assert result2["presence"] == "away"
            assert not await pool.fetchval(
                "SELECT count(*) FROM public.user_context "
                "WHERE signal_type = 'at_home' AND superseded_at IS NULL AND expires_at > now()"
            )

            # Stale feed: producer leaves state untouched (unknown).
            await pool.execute(
                "UPDATE ha_entity_snapshot SET state = 'home', last_updated = $1",
                datetime.now(UTC) - timedelta(hours=2),
            )
            result3 = await run_home_presence_context_producer(pool)
            assert result3["presence"] == "unknown"

            # HA outage: even a fresh-looking row (re-stamped captured_at, as
            # the real connector always does regardless of contact success)
            # must not assert or clear presence -- this is the trust-fix
            # defect itself (bu-8cdl1.12 slice 1).
            await pool.execute(
                "UPDATE ha_entity_snapshot SET state = 'home', captured_at = $1",
                datetime.now(UTC),
            )
            await pool.execute(
                "UPDATE ha_source_health SET status = 'error', updated_at = now() "
                "WHERE source = 'home_assistant'"
            )
            result4 = await run_home_presence_context_producer(pool)
            assert result4 == {"signal": None, "presence": "unmeasurable"}
            assert not await pool.fetchval(
                "SELECT count(*) FROM public.user_context "
                "WHERE signal_type = 'at_home' AND superseded_at IS NULL AND expires_at > now()"
            )
        finally:
            await pool.close()

    async def test_home_presence_producer_ignores_housemate_when_owner_absent(self, home_db_url):
        """bu-8cdl1.11 slice 1: a fresh housemate/guest entity must never assert at_home."""
        pool = await _pool(home_db_url)
        try:
            await self._mark_ha_source_healthy(pool)
            await state_set(pool, "home:presence:owner_entities", ["person.owner"])

            await pool.execute("TRUNCATE ha_entity_snapshot")
            now = datetime.now(UTC)
            await pool.execute(
                "INSERT INTO ha_entity_snapshot (entity_id, state, last_updated) VALUES "
                "('person.owner', 'not_home', $1), ('person.housemate', 'home', $1)",
                now,
            )
            result = await run_home_presence_context_producer(pool)
            assert result["presence"] == "away"
            assert not await pool.fetchval(
                "SELECT count(*) FROM public.user_context "
                "WHERE signal_type = 'at_home' AND superseded_at IS NULL AND expires_at > now()"
            )
        finally:
            await pool.close()

    async def test_home_presence_producer_reports_unmeasurable_on_ha_outage(self, home_db_url):
        """bu-8cdl1.11 slice 1: an unhealthy HA source reports unmeasurable, never a guess."""
        pool = await _pool(home_db_url)
        try:
            await state_set(pool, "home:presence:owner_entities", ["person.owner"])
            await self._mark_ha_source_healthy(pool)
            await pool.execute("TRUNCATE ha_entity_snapshot")
            await pool.execute(
                "INSERT INTO ha_entity_snapshot (entity_id, state, last_updated) "
                "VALUES ('person.owner', 'home', $1)",
                datetime.now(UTC),
            )
            # Owner is fresh-and-home while the source is healthy.
            result = await run_home_presence_context_producer(pool)
            assert result["presence"] == "home"

            # HA outage: even though ha_entity_snapshot still shows a fresh
            # "home" row, the producer must not keep asserting on it.
            await self._mark_ha_source_error(pool)
            result2 = await run_home_presence_context_producer(pool)
            assert result2["presence"] == "unmeasurable"
        finally:
            await pool.close()

    async def test_home_presence_producer_reports_unconfigured_without_owner_mapping(
        self, home_db_url
    ):
        """bu-8cdl1.11 slice 1: no owner mapping -> explicit unconfigured, not everyone-is-home."""
        pool = await _pool(home_db_url)
        try:
            await self._mark_ha_source_healthy(pool)
            await pool.execute("DELETE FROM state WHERE key = 'home:presence:owner_entities'")
            await pool.execute("TRUNCATE ha_entity_snapshot")
            await pool.execute(
                "INSERT INTO ha_entity_snapshot (entity_id, state, last_updated) "
                "VALUES ('person.housemate', 'home', $1)",
                datetime.now(UTC),
            )
            result = await run_home_presence_context_producer(pool)
            assert result["presence"] == "unconfigured"
            assert not await pool.fetchval(
                "SELECT count(*) FROM public.user_context "
                "WHERE signal_type = 'at_home' AND superseded_at IS NULL AND expires_at > now()"
            )
        finally:
            await pool.close()

    async def test_home_presence_producer_resolves_in_space_and_clears_on_departure(
        self, home_db_url
    ):
        """bu-8cdl1.11 slice 2: room-resolved occupancy alongside at_home."""
        pool = await _pool(home_db_url)
        try:
            await self._mark_ha_source_healthy(pool)
            await state_set(pool, "home:presence:owner_entities", ["person.owner"])
            await pool.execute("TRUNCATE ha_entity_snapshot")

            # Owner home, area resolved from HA attributes (generic "home" state).
            await pool.execute(
                "INSERT INTO ha_entity_snapshot (entity_id, state, attributes, last_updated) "
                "VALUES ('person.owner', 'home', $1, $2)",
                {"area": "kitchen"},
                datetime.now(UTC),
            )
            result = await run_home_presence_context_producer(pool)
            assert result["presence"] == "home"
            assert result["room"] == "kitchen"
            assert (
                await pool.fetchval(
                    "SELECT value FROM public.user_context "
                    "WHERE signal_type = 'in_space' AND set_by_butler = 'home' "
                    "AND superseded_at IS NULL AND expires_at > now()"
                )
                == "kitchen"
            )

            # A second owner-linked entity -- e.g. a BLE room-presence sensor --
            # reports the room directly as its state rather than via attributes.
            # person.owner keeps asserting at_home = home throughout; this entity
            # only refines *which room*, so it must never itself decide at_home.
            await state_set(
                pool, "home:presence:owner_entities", ["person.owner", "sensor.owner_room"]
            )
            await pool.execute(
                "UPDATE ha_entity_snapshot SET attributes = NULL WHERE entity_id = 'person.owner'"
            )
            await pool.execute(
                "INSERT INTO ha_entity_snapshot (entity_id, state, last_updated) "
                "VALUES ('sensor.owner_room', 'office', $1)",
                datetime.now(UTC),
            )
            result2 = await run_home_presence_context_producer(pool)
            assert result2["presence"] == "home"
            assert result2["room"] == "office"
            assert (
                await pool.fetchval(
                    "SELECT value FROM public.user_context "
                    "WHERE signal_type = 'in_space' AND set_by_butler = 'home' "
                    "AND superseded_at IS NULL AND expires_at > now()"
                )
                == "office"
            )

            # Owner leaves: both at_home and in_space clear, even though the
            # room-presence sensor still (harmlessly) reports a fresh reading.
            await pool.execute(
                "UPDATE ha_entity_snapshot SET state = 'not_home', last_updated = $1 "
                "WHERE entity_id = 'person.owner'",
                datetime.now(UTC),
            )
            result3 = await run_home_presence_context_producer(pool)
            assert result3["presence"] == "away"
            assert not await pool.fetchval(
                "SELECT count(*) FROM public.user_context "
                "WHERE signal_type IN ('at_home', 'in_space') "
                "AND superseded_at IS NULL AND expires_at > now()"
            )
        finally:
            await pool.close()

    async def test_home_presence_producer_in_space_degrades_to_unmeasurable_on_ha_outage(
        self, home_db_url
    ):
        """bu-8cdl1.11 slice 2: HA staleness degrades in_space, mirroring at_home (slice 1)."""
        pool = await _pool(home_db_url)
        try:
            await self._mark_ha_source_healthy(pool)
            await state_set(pool, "home:presence:owner_entities", ["person.owner"])
            await pool.execute("TRUNCATE ha_entity_snapshot")
            await pool.execute(
                "INSERT INTO ha_entity_snapshot (entity_id, state, attributes, last_updated) "
                "VALUES ('person.owner', 'home', $1, $2)",
                {"area": "kitchen"},
                datetime.now(UTC),
            )
            result = await run_home_presence_context_producer(pool)
            assert result["room"] == "kitchen"

            # HA outage: a fresh-looking room row must not be reported as current --
            # the producer reports unmeasurable and never computes a room at all,
            # the same early exit that already protects at_home (bu-8cdl1.12 slice 1).
            await self._mark_ha_source_error(pool)
            result2 = await run_home_presence_context_producer(pool)
            assert result2 == {"signal": None, "presence": "unmeasurable"}

            # The prior in_space assertion self-heals via its own bounded TTL
            # rather than being force-cleared -- identical to at_home's contract.
            assert (
                await pool.fetchval(
                    "SELECT value FROM public.user_context "
                    "WHERE signal_type = 'in_space' AND set_by_butler = 'home' "
                    "AND superseded_at IS NULL AND expires_at > now()"
                )
                == "kitchen"
            )
        finally:
            await pool.close()

    async def test_sleep_producer_activates_notify_suppression(self, core_db_url):
        """Closed-loop verification: producer -> notify gate sees the signal.

        With an owner-declared quiet window covering 'now', the sleep producer
        writes a `sleeping` signal, and `get_suppressing_context_signal` (the
        exact function the notify gate calls before delivery) now returns it —
        so the previously-dark `suppressed_context_bus` branch is reachable.
        """
        from butlers.core.attention_ledger import get_suppressing_context_signal

        pool = await _pool(core_db_url)
        try:
            # A two-hour UTC window centred on now keeps the live producer
            # inside the end-exclusive interval without relying on the removed
            # inclusive/full-day shorthand.
            current_hour = datetime.now(UTC).hour
            quiet_start = (current_hour - 1) % 24
            quiet_end = (current_hour + 1) % 24
            await pool.execute(
                "INSERT INTO public.approvals_policy (id, quiet_start_hour, quiet_end_hour, timezone) "
                "VALUES (1, $1, $2, 'UTC') "
                "ON CONFLICT (id) DO UPDATE SET "
                "quiet_start_hour = EXCLUDED.quiet_start_hour, "
                "quiet_end_hour = EXCLUDED.quiet_end_hour, timezone = 'UTC'",
                quiet_start,
                quiet_end,
            )
            # Before the producer runs, nothing suppresses.
            await _clear_non_dnd_context(pool)
            assert await get_suppressing_context_signal(pool) is None

            result = await run_sleep_window_context_producer(pool)
            assert result["signal"] == "sleeping"

            # The notify gate's own consult now sees the signal.
            assert await get_suppressing_context_signal(pool) == "sleeping"

            # Outside the window (no quiet policy) -> producer clears sleeping.
            await pool.execute(
                "UPDATE public.approvals_policy SET quiet_start_hour = NULL, quiet_end_hour = NULL"
            )
            result2 = await run_sleep_window_context_producer(pool)
            assert result2["signal"] is None
            assert await get_suppressing_context_signal(pool) is None
        finally:
            await pool.close()
