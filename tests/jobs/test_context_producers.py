"""Tests for the situational context-bus producers (RFC 0009, bu-hmdqz.15).

Two layers:
- Pure-logic unit tests for the deterministic classifiers/helpers
  (``classify_calendar_signal``, ``resolve_presence``, ``sleep_window_expiry``).
- Docker-gated integration tests that run each producer against a real,
  migration-accurate Postgres and assert it writes/clears ``public.user_context``
  correctly — including the closed-loop verification that the notify gate's
  ``get_suppressing_context_signal`` now sees a live ``sleeping`` signal, i.e.
  the previously-dark ``suppressed_context_bus`` branch is reachable.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.context_bus import ContextSignal
from butlers.db import register_jsonb_codec
from butlers.jobs.context_producers import (
    classify_calendar_signal,
    resolve_presence,
    run_calendar_context_producer,
    run_home_presence_context_producer,
    run_sleep_window_context_producer,
    run_travel_context_producer,
    sleep_window_expiry,
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


def test_resolve_presence():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    fresh = now - timedelta(minutes=5)
    stale = now - timedelta(hours=2)

    # A fresh presence entity reading "home" -> True
    assert (
        resolve_presence(
            [{"entity_id": "person.owner", "state": "home", "captured_at": fresh}],
            now=now,
        )
        is True
    )
    # Fresh entities, none home -> False (explicit away)
    assert (
        resolve_presence(
            [{"entity_id": "device_tracker.phone", "state": "not_home", "captured_at": fresh}],
            now=now,
        )
        is False
    )
    # Only a stale "home" reading -> unknown (never assert on a dead feed)
    assert (
        resolve_presence(
            [{"entity_id": "person.owner", "state": "home", "captured_at": stale}],
            now=now,
        )
        is None
    )
    # Non-presence entities are ignored -> unknown
    assert (
        resolve_presence(
            [{"entity_id": "sensor.kitchen_temp", "state": "home", "captured_at": fresh}],
            now=now,
        )
        is None
    )
    # Empty -> unknown
    assert resolve_presence([], now=now) is None
    # A fresh home reading wins even when another entity is away
    assert (
        resolve_presence(
            [
                {"entity_id": "device_tracker.phone", "state": "not_home", "captured_at": fresh},
                {"entity_id": "person.owner", "state": "home", "captured_at": fresh},
            ],
            now=now,
        )
        is True
    )


def test_sleep_window_expiry():
    # Overnight window 22..07 -> wake at 08:00; from 23:30 the next 08:00
    now = datetime(2026, 1, 1, 23, 30, tzinfo=UTC)
    assert sleep_window_expiry(now_local=now, quiet_end=7) == datetime(2026, 1, 2, 8, 0, tzinfo=UTC)
    # Same-day window ending at 12 -> wake at 13:00 same day
    now2 = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    assert sleep_window_expiry(now_local=now2, quiet_end=12) == datetime(
        2026, 1, 1, 13, 0, tzinfo=UTC
    )
    # quiet_end=23 -> wake at next midnight (hour wraps to 0)
    now3 = datetime(2026, 1, 1, 23, 45, tzinfo=UTC)
    assert sleep_window_expiry(now_local=now3, quiet_end=23) == datetime(
        2026, 1, 2, 0, 0, tzinfo=UTC
    )


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
    await p.execute("TRUNCATE public.user_context CASCADE")
    return p


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
                """
                INSERT INTO travel.trips (name, destination, start_date, end_date, status)
                VALUES ('Work trip', 'Tokyo', current_date - 1, current_date + 2, 'active')
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

    async def test_home_presence_producer_sets_and_clears(self, home_db_url):
        pool = await _pool(home_db_url)
        try:
            await pool.execute("TRUNCATE ha_entity_snapshot")
            await pool.execute(
                "INSERT INTO ha_entity_snapshot (entity_id, state, captured_at) "
                "VALUES ('person.owner', 'home', now())"
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
                "UPDATE ha_entity_snapshot SET state = 'not_home', captured_at = now()"
            )
            result2 = await run_home_presence_context_producer(pool)
            assert result2["presence"] == "away"
            assert not await pool.fetchval(
                "SELECT count(*) FROM public.user_context "
                "WHERE signal_type = 'at_home' AND superseded_at IS NULL AND expires_at > now()"
            )

            # Stale feed: producer leaves state untouched (unknown).
            await pool.execute(
                "UPDATE ha_entity_snapshot SET state = 'home', captured_at = now() - interval '2 hours'"
            )
            result3 = await run_home_presence_context_producer(pool)
            assert result3["presence"] == "unknown"
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
            # Whole-day quiet window in UTC -> 'now' is always inside it.
            await pool.execute(
                "INSERT INTO public.approvals_policy (id, quiet_start_hour, quiet_end_hour, timezone) "
                "VALUES (1, 0, 23, 'UTC') "
                "ON CONFLICT (id) DO UPDATE SET "
                "quiet_start_hour = 0, quiet_end_hour = 23, timezone = 'UTC'"
            )
            # Before the producer runs, nothing suppresses.
            await pool.execute("TRUNCATE public.user_context CASCADE")
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
