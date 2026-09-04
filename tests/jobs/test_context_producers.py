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

import json
import shutil
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from butlers.context_bus import ContextSignal
from butlers.core.state import state_set
from butlers.db import register_jsonb_codec
from butlers.jobs.context_producers import (
    classify_calendar_signal,
    resolve_owner_presence,
    run_calendar_context_producer,
    run_home_presence_context_producer,
    run_sleep_window_context_producer,
    run_travel_context_producer,
)
from butlers.testing.migration import create_migrated_test_db, migration_db_name

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


async def test_calendar_context_producer_skips_explicit_butler_generated_event_but_keeps_human_event():
    now = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    pool = MagicMock()
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


async def test_calendar_context_producer_treats_legacy_midnight_block_as_non_meeting():
    singapore = ZoneInfo("Asia/Singapore")
    starts_at = datetime(2026, 7, 1, 0, 0, tzinfo=singapore)
    pool = MagicMock()
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
    pool = MagicMock()
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
    async def test_calendar_producer_sets_meeting_then_clears(self, core_db_url):
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

    async def test_calendar_producer_classifies_focus_block(self, core_db_url):
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
