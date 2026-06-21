"""Tests for owner scheduling-availability preferences (life/meeting hours).

Covers:
- SchedulingPreferences.allows: earliest/latest/weekday/no-meeting-block filtering
- SchedulingPreferences.from_row + has_constraints + timezone localisation
- get_scheduling_preferences: None on missing table (older schema)
- upsert/get round-trip + invalid timezone rejection (DB-backed, docker-gated)

These exercise the modeling decision recorded in
openspec/changes/calendar-availability-find-time/design.md (D3): owner
scheduling-availability is DISTINCT from per-butler notification quiet hours.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, time
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from butlers.core.temporal.scheduling import (
    SchedulingPreferences,
    get_scheduling_preferences,
)
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [pytest.mark.unit]

docker_available = shutil.which("docker") is not None


def _dt(year, month, day, hour, minute=0, tz="UTC") -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz))


class TestSchedulingPreferencesAllows:
    def test_empty_prefs_allow_everything(self):
        prefs = SchedulingPreferences()
        assert prefs.has_constraints is False
        # 6am on a Sunday — would be rejected with constraints, allowed without.
        assert prefs.allows(_dt(2026, 6, 21, 6), _dt(2026, 6, 21, 7)) is True

    def test_earliest_and_latest_meeting_time(self):
        prefs = SchedulingPreferences(
            timezone="UTC",
            earliest_meeting_time=time(9, 0),
            latest_meeting_time=time(18, 0),
        )
        assert prefs.has_constraints is True
        # Mon 2026-06-22
        assert prefs.allows(_dt(2026, 6, 22, 6), _dt(2026, 6, 22, 7)) is False  # before earliest
        assert prefs.allows(_dt(2026, 6, 22, 9), _dt(2026, 6, 22, 10)) is True
        assert (
            prefs.allows(_dt(2026, 6, 22, 17, 30), _dt(2026, 6, 22, 18, 30)) is False
        )  # past latest
        assert (
            prefs.allows(_dt(2026, 6, 22, 17), _dt(2026, 6, 22, 18)) is True
        )  # ends exactly at latest

    def test_meeting_days_excludes_weekends(self):
        prefs = SchedulingPreferences(
            timezone="UTC",
            meeting_days=frozenset({"MO", "TU", "WE", "TH", "FR"}),
        )
        # Sun 2026-06-21 rejected; Mon 2026-06-22 allowed.
        assert prefs.allows(_dt(2026, 6, 21, 10), _dt(2026, 6, 21, 11)) is False
        assert prefs.allows(_dt(2026, 6, 22, 10), _dt(2026, 6, 22, 11)) is True

    def test_no_meeting_block_overlap_rejected(self):
        prefs = SchedulingPreferences(
            timezone="UTC",
            no_meeting_blocks=((time(12, 0), time(13, 0)),),
        )
        # Overlapping lunch
        assert prefs.allows(_dt(2026, 6, 22, 12, 30), _dt(2026, 6, 22, 13, 30)) is False
        assert prefs.allows(_dt(2026, 6, 22, 11, 30), _dt(2026, 6, 22, 12, 30)) is False
        # Adjacent (ends exactly at block start / starts at block end) — allowed
        assert prefs.allows(_dt(2026, 6, 22, 11), _dt(2026, 6, 22, 12)) is True
        assert prefs.allows(_dt(2026, 6, 22, 13), _dt(2026, 6, 22, 14)) is True

    def test_timezone_localisation(self):
        # earliest 09:00 in New York; an instant that is 13:00 UTC == 09:00 EDT.
        prefs = SchedulingPreferences(
            timezone="America/New_York",
            earliest_meeting_time=time(9, 0),
        )
        # 12:00 UTC == 08:00 EDT -> before earliest -> rejected
        assert prefs.allows(_dt(2026, 6, 22, 12), _dt(2026, 6, 22, 13)) is False
        # 13:00 UTC == 09:00 EDT -> allowed
        assert prefs.allows(_dt(2026, 6, 22, 13), _dt(2026, 6, 22, 14)) is True


class TestSchedulingPreferencesFromRow:
    def test_from_row_none_returns_none(self):
        assert SchedulingPreferences.from_row(None) is None
        assert SchedulingPreferences.from_row({}) is None

    def test_from_row_parses_all_fields(self):
        prefs = SchedulingPreferences.from_row(
            {
                "timezone": "Europe/Berlin",
                "earliest_meeting_time": "09:00",
                "latest_meeting_time": "18:00",
                "meeting_days": ["MO", "tu", "WE"],  # mixed-case normalised
                "no_meeting_blocks": [{"start": "12:00", "end": "13:00"}],
            }
        )
        assert prefs is not None
        assert prefs.timezone == "Europe/Berlin"
        assert prefs.earliest_meeting_time == time(9, 0)
        assert prefs.latest_meeting_time == time(18, 0)
        assert prefs.meeting_days == frozenset({"MO", "TU", "WE"})
        assert prefs.no_meeting_blocks == ((time(12, 0), time(13, 0)),)
        assert prefs.has_constraints is True

    def test_from_row_no_constraints(self):
        prefs = SchedulingPreferences.from_row({"timezone": "UTC", "no_meeting_blocks": []})
        assert prefs is not None
        assert prefs.has_constraints is False

    def test_from_row_rejects_bad_weekday(self):
        with pytest.raises(ValueError, match="Invalid weekday"):
            SchedulingPreferences.from_row({"meeting_days": ["MO", "FUNDAY"]})


class TestGetSchedulingPreferencesMissingTable:
    async def test_missing_table_returns_none(self):
        """Older schema-scoped DBs may not have the table yet -> None (no constraints)."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=asyncpg.exceptions.UndefinedTableError(
                'relation "owner_scheduling_preferences" does not exist'
            )
        )
        assert await get_scheduling_preferences(pool) is None


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision a DB with core migrations applied once per module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
class TestSchedulingPreferencesDB:
    @pytest.fixture
    async def pool(self, migrated_db_url: str):
        p = await asyncpg.create_pool(
            migrated_db_url, min_size=1, max_size=3, init=register_jsonb_codec
        )
        await p.execute("TRUNCATE public.owner_scheduling_preferences CASCADE")
        yield p
        await p.close()

    async def test_upsert_and_get_round_trip(self, pool):
        from butlers.core.temporal.scheduling import upsert_scheduling_preferences

        # None before any upsert
        assert await get_scheduling_preferences(pool) is None

        result = await upsert_scheduling_preferences(
            pool,
            timezone="America/New_York",
            earliest_meeting_time="09:00",
            latest_meeting_time="18:00",
            meeting_days=["MO", "TU", "WE", "TH", "FR"],
            no_meeting_blocks=[{"start": "12:00", "end": "13:00"}],
        )
        assert result["timezone"] == "America/New_York"
        assert result["earliest_meeting_time"] == "09:00"
        assert result["meeting_days"] == ["MO", "TU", "WE", "TH", "FR"]
        assert result["no_meeting_blocks"] == [{"start": "12:00", "end": "13:00"}]

        # Singleton: a second upsert updates the same row, never inserts a second.
        await upsert_scheduling_preferences(pool, timezone="Europe/London")
        count = await pool.fetchval("SELECT COUNT(*) FROM public.owner_scheduling_preferences")
        assert count == 1
        prefs = await get_scheduling_preferences(pool)
        assert prefs["timezone"] == "Europe/London"
        # Previously-set fields are preserved across partial update.
        assert prefs["earliest_meeting_time"] == "09:00"

    async def test_invalid_timezone_rejected(self, pool):
        from butlers.core.temporal.scheduling import upsert_scheduling_preferences

        with pytest.raises(ValueError, match="Unknown timezone"):
            await upsert_scheduling_preferences(pool, timezone="Invalid/Zone")

    async def test_loads_into_scheduling_preferences(self, pool):
        from butlers.core.temporal.scheduling import upsert_scheduling_preferences

        await upsert_scheduling_preferences(
            pool,
            timezone="UTC",
            earliest_meeting_time="09:00",
            latest_meeting_time="17:00",
            meeting_days=["MO", "TU", "WE", "TH", "FR"],
        )
        prefs = SchedulingPreferences.from_row(await get_scheduling_preferences(pool))
        assert prefs is not None and prefs.has_constraints
        # Sunday rejected, weekday morning allowed.
        assert (
            prefs.allows(
                datetime(2026, 6, 21, 10, tzinfo=UTC),
                datetime(2026, 6, 21, 11, tzinfo=UTC),
            )
            is False
        )
        assert (
            prefs.allows(
                datetime(2026, 6, 22, 10, tzinfo=UTC),
                datetime(2026, 6, 22, 11, tzinfo=UTC),
            )
            is True
        )
