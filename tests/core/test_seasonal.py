"""Integration tests for butlers.core.seasonal — DB-backed CRUD and active-period detection.

Tests:
- get_active_seasons: same-year, cross-year, disabled, multiple concurrent
- seasonal_period_create: basic CRUD, duplicate name, invalid dates
- seasonal_period_update: field updates, invalid dates, not-found
- seasonal_period_list: is_active field, disabled periods
- seasonal_period_delete: removal, not-found, butler scoping
- seasonal_period_create_preset: all presets, unknown preset, duplicate
- tick() seasonal context injection: seasonal prefix prepended to prompt / not injected
"""

from __future__ import annotations

import shutil
import uuid
from datetime import date

import asyncpg
import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


_SEASONAL_TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS seasonal_periods (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name TEXT NOT NULL,
        period_type TEXT NOT NULL DEFAULT 'annual' CHECK (period_type IN (
            'annual', 'academic', 'fiscal', 'custom'
        )),
        start_month INTEGER NOT NULL CHECK (start_month BETWEEN 1 AND 12),
        start_day   INTEGER NOT NULL CHECK (start_day   BETWEEN 1 AND 31),
        end_month   INTEGER NOT NULL CHECK (end_month   BETWEEN 1 AND 12),
        end_day     INTEGER NOT NULL CHECK (end_day     BETWEEN 1 AND 31),
        timezone    TEXT NOT NULL DEFAULT 'UTC',
        metadata    JSONB,
        butler_name TEXT NOT NULL,
        enabled     BOOLEAN NOT NULL DEFAULT true,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT seasonal_periods_name_butler_unique UNIQUE (name, butler_name)
    )
"""

_SCHEDULED_TASKS_DDL = """
    CREATE TABLE IF NOT EXISTS scheduled_tasks (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name TEXT UNIQUE NOT NULL,
        cron TEXT NOT NULL,
        prompt TEXT,
        dispatch_mode TEXT NOT NULL DEFAULT 'prompt',
        job_name TEXT,
        job_args JSONB,
        complexity TEXT DEFAULT 'medium',
        timezone TEXT NOT NULL DEFAULT 'UTC',
        start_at TIMESTAMPTZ,
        end_at TIMESTAMPTZ,
        until_at TIMESTAMPTZ,
        display_title TEXT,
        calendar_event_id TEXT,
        source TEXT NOT NULL DEFAULT 'db',
        enabled BOOLEAN NOT NULL DEFAULT true,
        next_run_at TIMESTAMPTZ,
        last_run_at TIMESTAMPTZ,
        last_result JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT scheduled_tasks_dispatch_mode_check
            CHECK (dispatch_mode IN ('prompt', 'job')),
        CONSTRAINT scheduled_tasks_dispatch_payload_check
            CHECK (
                (dispatch_mode = 'prompt' AND prompt IS NOT NULL AND job_name IS NULL)
                OR (dispatch_mode = 'job' AND job_name IS NOT NULL)
            )
    )
"""


async def _make_pool(postgres_container, *ddl_statements: str) -> asyncpg.Pool:
    db_name = _unique_db_name()
    admin_conn = await asyncpg.connect(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database="postgres",
    )
    try:
        safe_name = db_name.replace('"', '""')
        await admin_conn.execute(f'CREATE DATABASE "{safe_name}"')
    finally:
        await admin_conn.close()

    pool = await asyncpg.create_pool(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database=db_name,
        min_size=1,
        max_size=3,
    )
    for ddl in ddl_statements:
        await pool.execute(ddl)
    return pool


@pytest.fixture
async def pool(postgres_container):
    """Fresh DB with seasonal_periods table."""
    p = await _make_pool(postgres_container, _SEASONAL_TABLE_DDL)
    yield p
    await p.close()


@pytest.fixture
async def scheduler_pool(postgres_container):
    """Fresh DB with both scheduled_tasks and seasonal_periods tables."""
    p = await _make_pool(postgres_container, _SCHEDULED_TASKS_DDL, _SEASONAL_TABLE_DDL)
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# get_active_seasons — same year
# ---------------------------------------------------------------------------


async def test_get_active_seasons_same_year_active(pool):
    """Period within same year is returned when today is in range."""
    from butlers.core.seasonal import get_active_seasons, seasonal_period_create

    await seasonal_period_create(
        pool,
        "testbutler",
        "tax-season",
        "fiscal",
        start_month=1,
        start_day=1,
        end_month=4,
        end_day=15,
    )

    # March 15 is inside Jan 1 – Apr 15
    active = await get_active_seasons(pool, "testbutler", today=date(2025, 3, 15))
    assert len(active) == 1
    assert active[0]["name"] == "tax-season"


async def test_get_active_seasons_same_year_inactive(pool):
    """Period within same year is not returned when today is outside range."""
    from butlers.core.seasonal import get_active_seasons, seasonal_period_create

    await seasonal_period_create(
        pool,
        "testbutler2",
        "tax-season",
        "fiscal",
        start_month=1,
        start_day=1,
        end_month=4,
        end_day=15,
    )

    # June 1 is outside Jan 1 – Apr 15
    active = await get_active_seasons(pool, "testbutler2", today=date(2025, 6, 1))
    assert len(active) == 0


# ---------------------------------------------------------------------------
# get_active_seasons — year-boundary wrapping
# ---------------------------------------------------------------------------


async def test_get_active_seasons_cross_year_active(pool):
    """Cross-year period is active on Dec 20."""
    from butlers.core.seasonal import get_active_seasons, seasonal_period_create

    await seasonal_period_create(
        pool,
        "testbutler3",
        "winter-holidays",
        "annual",
        start_month=11,
        start_day=15,
        end_month=1,
        end_day=10,
    )

    active = await get_active_seasons(pool, "testbutler3", today=date(2025, 12, 20))
    assert len(active) == 1
    assert active[0]["name"] == "winter-holidays"


async def test_get_active_seasons_cross_year_active_jan(pool):
    """Cross-year period is active in January (before the end date)."""
    from butlers.core.seasonal import get_active_seasons, seasonal_period_create

    await seasonal_period_create(
        pool,
        "testbutler3b",
        "winter-holidays",
        "annual",
        start_month=11,
        start_day=15,
        end_month=1,
        end_day=10,
    )

    active = await get_active_seasons(pool, "testbutler3b", today=date(2025, 1, 5))
    assert len(active) == 1


async def test_get_active_seasons_cross_year_inactive_mid_year(pool):
    """Cross-year period is not active in mid-year."""
    from butlers.core.seasonal import get_active_seasons, seasonal_period_create

    await seasonal_period_create(
        pool,
        "testbutler4",
        "winter-holidays",
        "annual",
        start_month=11,
        start_day=15,
        end_month=1,
        end_day=10,
    )

    active = await get_active_seasons(pool, "testbutler4", today=date(2025, 6, 15))
    assert len(active) == 0


# ---------------------------------------------------------------------------
# get_active_seasons — disabled periods excluded
# ---------------------------------------------------------------------------


async def test_get_active_seasons_disabled_excluded(pool):
    """Disabled periods are excluded from get_active_seasons()."""
    from butlers.core.seasonal import get_active_seasons, seasonal_period_create

    await seasonal_period_create(
        pool,
        "testbutler5",
        "disabled-period",
        "annual",
        start_month=1,
        start_day=1,
        end_month=12,
        end_day=31,
        enabled=False,
    )

    active = await get_active_seasons(pool, "testbutler5", today=date(2025, 6, 15))
    assert len(active) == 0


# ---------------------------------------------------------------------------
# get_active_seasons — multiple concurrent periods
# ---------------------------------------------------------------------------


async def test_get_active_seasons_multiple_concurrent(pool):
    """Multiple overlapping active periods are all returned."""
    from butlers.core.seasonal import get_active_seasons, seasonal_period_create

    butler = "testbutler6"
    # Jan 20 is in both tax-season (Jan 1 – Apr 15) and
    # winter-holidays (Nov 15 – Jan 31 cross-year)
    await seasonal_period_create(
        pool,
        butler,
        "tax-season",
        "fiscal",
        start_month=1,
        start_day=1,
        end_month=4,
        end_day=15,
    )
    await seasonal_period_create(
        pool,
        butler,
        "winter-holidays",
        "annual",
        start_month=11,
        start_day=15,
        end_month=1,
        end_day=31,
    )

    active = await get_active_seasons(pool, butler, today=date(2025, 1, 20))
    names = {p["name"] for p in active}
    assert "tax-season" in names
    assert "winter-holidays" in names


async def test_get_active_seasons_empty_when_no_periods(pool):
    """No periods defined returns empty list."""
    from butlers.core.seasonal import get_active_seasons

    active = await get_active_seasons(pool, "no-periods-butler", today=date(2025, 6, 1))
    assert active == []


# ---------------------------------------------------------------------------
# seasonal_period_create
# ---------------------------------------------------------------------------


async def test_create_returns_uuid(pool):
    """seasonal_period_create returns a valid UUID."""
    from butlers.core.seasonal import seasonal_period_create

    period_id = await seasonal_period_create(
        pool,
        "butler-a",
        "spring-cleaning",
        "annual",
        start_month=3,
        start_day=1,
        end_month=5,
        end_day=31,
    )
    assert isinstance(period_id, uuid.UUID)


async def test_create_duplicate_name_raises(pool):
    """Duplicate name for the same butler raises ValueError."""
    from butlers.core.seasonal import seasonal_period_create

    butler = "butler-dup"
    await seasonal_period_create(
        pool,
        butler,
        "my-season",
        "annual",
        start_month=1,
        start_day=1,
        end_month=3,
        end_day=31,
    )
    with pytest.raises(ValueError, match="already exists"):
        await seasonal_period_create(
            pool,
            butler,
            "my-season",
            "annual",
            start_month=6,
            start_day=1,
            end_month=8,
            end_day=31,
        )


async def test_create_different_butler_same_name_ok(pool):
    """Same name is allowed for different butlers."""
    from butlers.core.seasonal import seasonal_period_create

    id1 = await seasonal_period_create(
        pool,
        "butler-x",
        "my-season",
        "annual",
        start_month=1,
        start_day=1,
        end_month=3,
        end_day=31,
    )
    id2 = await seasonal_period_create(
        pool,
        "butler-y",
        "my-season",
        "annual",
        start_month=1,
        start_day=1,
        end_month=3,
        end_day=31,
    )
    assert id1 != id2


async def test_create_invalid_month_day_raises(pool):
    """Feb 30 is rejected at create time."""
    from butlers.core.seasonal import seasonal_period_create

    with pytest.raises(ValueError, match="February"):
        await seasonal_period_create(
            pool,
            "butler-b",
            "bad-period",
            "annual",
            start_month=2,
            start_day=30,
            end_month=3,
            end_day=15,
        )


async def test_create_invalid_period_type_raises(pool):
    """Unknown period_type is rejected."""
    from butlers.core.seasonal import seasonal_period_create

    with pytest.raises(ValueError, match="period_type"):
        await seasonal_period_create(
            pool,
            "butler-c",
            "weird-period",
            "quarterly",
            start_month=1,
            start_day=1,
            end_month=3,
            end_day=31,
        )


async def test_create_with_metadata_round_trips(pool):
    """metadata JSONB round-trips correctly."""
    from butlers.core.seasonal import seasonal_period_create, seasonal_period_list

    butler = "butler-meta"
    meta = {"context_hint": "Tax time!", "priority_boost": 2}
    await seasonal_period_create(
        pool,
        butler,
        "tax-meta",
        "fiscal",
        start_month=1,
        start_day=1,
        end_month=4,
        end_day=15,
        metadata=meta,
    )

    periods = await seasonal_period_list(pool, butler)
    assert len(periods) == 1
    assert periods[0]["metadata"] == meta


async def test_create_enabled_false(pool):
    """Creating with enabled=False stores that state."""
    from butlers.core.seasonal import seasonal_period_create, seasonal_period_list

    butler = "butler-disabled"
    await seasonal_period_create(
        pool,
        butler,
        "off-period",
        "annual",
        start_month=1,
        start_day=1,
        end_month=12,
        end_day=31,
        enabled=False,
    )

    periods = await seasonal_period_list(pool, butler)
    assert len(periods) == 1
    assert periods[0]["enabled"] is False


# ---------------------------------------------------------------------------
# seasonal_period_update
# ---------------------------------------------------------------------------


async def test_update_enabled_flag(pool):
    """Update enabled=False disables the period."""
    from butlers.core.seasonal import (
        get_active_seasons,
        seasonal_period_create,
        seasonal_period_update,
    )

    butler = "butler-upd"
    period_id = await seasonal_period_create(
        pool,
        butler,
        "year-round",
        "annual",
        start_month=1,
        start_day=1,
        end_month=12,
        end_day=31,
    )

    # Initially active
    active = await get_active_seasons(pool, butler, today=date(2025, 6, 15))
    assert len(active) == 1

    found = await seasonal_period_update(pool, butler, period_id=period_id, enabled=False)
    assert found is True

    # Now excluded
    active = await get_active_seasons(pool, butler, today=date(2025, 6, 15))
    assert len(active) == 0


async def test_update_start_date(pool):
    """Start month/day update is persisted."""
    from butlers.core.seasonal import (
        seasonal_period_create,
        seasonal_period_list,
        seasonal_period_update,
    )

    butler = "butler-upd2"
    period_id = await seasonal_period_create(
        pool,
        butler,
        "my-period",
        "annual",
        start_month=1,
        start_day=1,
        end_month=4,
        end_day=15,
    )

    await seasonal_period_update(pool, butler, period_id=period_id, start_month=2, start_day=1)

    periods = await seasonal_period_list(pool, butler)
    assert periods[0]["start_month"] == 2
    assert periods[0]["start_day"] == 1


async def test_update_invalid_date_raises(pool):
    """Updating to an invalid month/day combo raises ValueError."""
    from butlers.core.seasonal import seasonal_period_create, seasonal_period_update

    butler = "butler-upd3"
    period_id = await seasonal_period_create(
        pool,
        butler,
        "my-period",
        "annual",
        start_month=1,
        start_day=1,
        end_month=4,
        end_day=15,
    )

    with pytest.raises(ValueError, match="February"):
        await seasonal_period_update(pool, butler, period_id=period_id, start_month=2, start_day=30)


async def test_update_not_found_returns_false(pool):
    """Updating a non-existent period returns False."""
    from butlers.core.seasonal import seasonal_period_update

    fake_id = uuid.uuid4()
    found = await seasonal_period_update(pool, "any-butler", period_id=fake_id, enabled=True)
    assert found is False


async def test_update_name(pool):
    """Updating the name renames the period."""
    from butlers.core.seasonal import (
        seasonal_period_create,
        seasonal_period_list,
        seasonal_period_update,
    )

    butler = "butler-rename"
    period_id = await seasonal_period_create(
        pool,
        butler,
        "old-name",
        "annual",
        start_month=1,
        start_day=1,
        end_month=3,
        end_day=31,
    )

    await seasonal_period_update(pool, butler, period_id=period_id, name="new-name")

    periods = await seasonal_period_list(pool, butler)
    assert periods[0]["name"] == "new-name"


async def test_update_scoped_to_butler(pool):
    """Update on wrong butler returns False (no cross-butler mutation)."""
    from butlers.core.seasonal import seasonal_period_create, seasonal_period_update

    period_id = await seasonal_period_create(
        pool,
        "owner-butler",
        "my-period",
        "annual",
        start_month=1,
        start_day=1,
        end_month=3,
        end_day=31,
    )

    found = await seasonal_period_update(pool, "wrong-butler", period_id=period_id, enabled=False)
    assert found is False


async def test_update_unspecified_date_fields_preserved(pool):
    """Partial update preserves date fields that are not explicitly provided.

    Regression guard for the bug where the SET clause unconditionally included
    all four date fields even when the caller did not supply them.
    """
    from butlers.core.seasonal import (
        seasonal_period_create,
        seasonal_period_list,
        seasonal_period_update,
    )

    butler = "butler-partial-upd"
    period_id = await seasonal_period_create(
        pool,
        butler,
        "my-period",
        "annual",
        start_month=3,
        start_day=5,
        end_month=6,
        end_day=20,
    )

    # Only update `enabled` — all date fields must remain unchanged.
    await seasonal_period_update(pool, butler, period_id=period_id, enabled=False)

    periods = await seasonal_period_list(pool, butler)
    assert len(periods) == 1
    p = periods[0]
    assert p["start_month"] == 3
    assert p["start_day"] == 5
    assert p["end_month"] == 6
    assert p["end_day"] == 20
    assert p["enabled"] is False


# ---------------------------------------------------------------------------
# seasonal_period_list
# ---------------------------------------------------------------------------


async def test_list_includes_is_active(pool):
    """seasonal_period_list includes is_active field."""
    from butlers.core.seasonal import seasonal_period_create, seasonal_period_list

    butler = "butler-list"
    await seasonal_period_create(
        pool,
        butler,
        "active-period",
        "annual",
        start_month=1,
        start_day=1,
        end_month=12,
        end_day=31,
    )
    await seasonal_period_create(
        pool,
        butler,
        "narrow-period",
        "annual",
        start_month=3,
        start_day=1,
        end_month=3,
        end_day=31,
    )

    # June is active for year-round but not narrow (March only)
    periods = await seasonal_period_list(pool, butler, today=date(2025, 6, 15))
    by_name = {p["name"]: p for p in periods}

    assert by_name["active-period"]["is_active"] is True
    assert by_name["narrow-period"]["is_active"] is False


async def test_list_disabled_period_not_active(pool):
    """Disabled periods always have is_active=False."""
    from butlers.core.seasonal import seasonal_period_create, seasonal_period_list

    butler = "butler-list2"
    await seasonal_period_create(
        pool,
        butler,
        "disabled-period",
        "annual",
        start_month=1,
        start_day=1,
        end_month=12,
        end_day=31,
        enabled=False,
    )

    periods = await seasonal_period_list(pool, butler, today=date(2025, 6, 15))
    assert len(periods) == 1
    assert periods[0]["is_active"] is False


async def test_list_empty_for_unknown_butler(pool):
    """Empty list for a butler with no periods."""
    from butlers.core.seasonal import seasonal_period_list

    periods = await seasonal_period_list(pool, "nobody")
    assert periods == []


# ---------------------------------------------------------------------------
# seasonal_period_delete
# ---------------------------------------------------------------------------


async def test_delete_removes_period(pool):
    """Deleted period is no longer listed."""
    from butlers.core.seasonal import (
        seasonal_period_create,
        seasonal_period_delete,
        seasonal_period_list,
    )

    butler = "butler-del"
    period_id = await seasonal_period_create(
        pool,
        butler,
        "to-delete",
        "annual",
        start_month=1,
        start_day=1,
        end_month=3,
        end_day=31,
    )

    found = await seasonal_period_delete(pool, butler, period_id=period_id)
    assert found is True

    periods = await seasonal_period_list(pool, butler)
    assert len(periods) == 0


async def test_delete_not_found_returns_false(pool):
    """Deleting a non-existent period returns False."""
    from butlers.core.seasonal import seasonal_period_delete

    fake_id = uuid.uuid4()
    found = await seasonal_period_delete(pool, "any-butler", period_id=fake_id)
    assert found is False


async def test_delete_scoped_to_butler(pool):
    """Delete does not affect another butler's period."""
    from butlers.core.seasonal import (
        seasonal_period_create,
        seasonal_period_delete,
        seasonal_period_list,
    )

    id1 = await seasonal_period_create(
        pool,
        "butler-del-a",
        "shared-name",
        "annual",
        start_month=1,
        start_day=1,
        end_month=3,
        end_day=31,
    )
    await seasonal_period_create(
        pool,
        "butler-del-b",
        "shared-name",
        "annual",
        start_month=1,
        start_day=1,
        end_month=3,
        end_day=31,
    )

    await seasonal_period_delete(pool, "butler-del-a", period_id=id1)

    # butler-del-b's period survives
    periods_b = await seasonal_period_list(pool, "butler-del-b")
    assert len(periods_b) == 1


# ---------------------------------------------------------------------------
# seasonal_period_create_preset
# ---------------------------------------------------------------------------


async def test_create_preset_us_tax_season(pool):
    """us-tax-season preset creates a period with correct dates."""
    from butlers.core.seasonal import seasonal_period_create_preset, seasonal_period_list

    butler = "butler-preset"
    await seasonal_period_create_preset(pool, butler, preset="us-tax-season")

    periods = await seasonal_period_list(pool, butler)
    assert len(periods) == 1
    p = periods[0]
    assert p["name"] == "us-tax-season"
    assert p["period_type"] == "fiscal"
    assert p["start_month"] == 1
    assert p["start_day"] == 1
    assert p["end_month"] == 4
    assert p["end_day"] == 15
    assert p["metadata"] is not None
    assert "context_hint" in p["metadata"]


async def test_create_preset_year_end_holidays(pool):
    """year-end-holidays preset creates a cross-year period."""
    from butlers.core.seasonal import seasonal_period_create_preset, seasonal_period_list

    butler = "butler-preset2"
    await seasonal_period_create_preset(pool, butler, preset="year-end-holidays")

    periods = await seasonal_period_list(pool, butler)
    assert len(periods) == 1
    p = periods[0]
    assert p["start_month"] == 12
    assert p["end_month"] == 1


async def test_create_all_presets(pool):
    """All five presets can be created successfully."""
    from butlers.core.seasonal import (
        SEASONAL_PRESETS,
        seasonal_period_create_preset,
        seasonal_period_list,
    )

    butler = "butler-all-presets"
    for preset_name in SEASONAL_PRESETS:
        await seasonal_period_create_preset(pool, butler, preset=preset_name)

    periods = await seasonal_period_list(pool, butler)
    created_names = {p["name"] for p in periods}
    assert created_names == set(SEASONAL_PRESETS.keys())


async def test_create_preset_unknown_raises(pool):
    """Unknown preset raises ValueError listing available presets."""
    from butlers.core.seasonal import seasonal_period_create_preset

    with pytest.raises(ValueError, match="Unknown preset"):
        await seasonal_period_create_preset(pool, "any-butler", preset="nonexistent-season")


async def test_create_preset_duplicate_raises(pool):
    """Creating the same preset twice raises ValueError."""
    from butlers.core.seasonal import seasonal_period_create_preset

    butler = "butler-preset-dup"
    await seasonal_period_create_preset(pool, butler, preset="fall-semester")
    with pytest.raises(ValueError, match="already exists"):
        await seasonal_period_create_preset(pool, butler, preset="fall-semester")


# ---------------------------------------------------------------------------
# tick() seasonal context injection
# ---------------------------------------------------------------------------


class _Dispatch:
    """Captures dispatch calls for assertions."""

    def __init__(self, *, result=None):
        self.calls: list[dict] = []
        self._result = result

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self._result


async def test_tick_injects_active_seasons(scheduler_pool):
    """tick() prepends seasonal context to the prompt when seasons are active.

    The seasonal prefix is added directly to the prompt string rather than
    passed as a separate kwarg, keeping dispatch_fn (Spawner.trigger) signature
    unchanged.
    """
    from butlers.core.scheduler import tick
    from butlers.core.seasonal import seasonal_period_create

    butler = "tick-butler"

    await scheduler_pool.execute("""
        INSERT INTO scheduled_tasks (name, cron, dispatch_mode, prompt, next_run_at)
        VALUES ('task1', '* * * * *', 'prompt', 'Do something', now() - interval '1 minute')
    """)

    # Insert a year-round active period
    await seasonal_period_create(
        scheduler_pool,
        butler,
        "year-round",
        "annual",
        start_month=1,
        start_day=1,
        end_month=12,
        end_day=31,
    )

    dispatch = _Dispatch()
    await tick(scheduler_pool, dispatch, butler_name=butler)

    assert len(dispatch.calls) == 1
    call = dispatch.calls[0]
    # Seasonal context is prepended to the prompt, not passed as a separate kwarg
    assert "active_seasons" not in call
    assert "year-round" in call["prompt"]
    assert "Seasonal context" in call["prompt"]
    assert "Do something" in call["prompt"]


async def test_tick_no_active_seasons_prompt_unchanged(scheduler_pool):
    """tick() does not modify the prompt when no seasons are active."""
    from butlers.core.scheduler import tick

    butler = "tick-butler2"

    await scheduler_pool.execute("""
        INSERT INTO scheduled_tasks (name, cron, dispatch_mode, prompt, next_run_at)
        VALUES ('task2', '* * * * *', 'prompt', 'Do something', now() - interval '1 minute')
    """)
    # No seasonal periods for this butler

    dispatch = _Dispatch()
    await tick(scheduler_pool, dispatch, butler_name=butler)

    assert len(dispatch.calls) == 1
    call = dispatch.calls[0]
    assert call["prompt"] == "Do something"


async def test_tick_no_butler_name_no_injection(scheduler_pool):
    """tick() without butler_name does not modify the prompt."""
    from butlers.core.scheduler import tick
    from butlers.core.seasonal import seasonal_period_create

    butler = "tick-butler3"

    await scheduler_pool.execute("""
        INSERT INTO scheduled_tasks (name, cron, dispatch_mode, prompt, next_run_at)
        VALUES ('task3', '* * * * *', 'prompt', 'Do something', now() - interval '1 minute')
    """)

    await seasonal_period_create(
        scheduler_pool,
        butler,
        "year-round",
        "annual",
        start_month=1,
        start_day=1,
        end_month=12,
        end_day=31,
    )

    dispatch = _Dispatch()
    # No butler_name passed
    await tick(scheduler_pool, dispatch)

    assert len(dispatch.calls) == 1
    call = dispatch.calls[0]
    # No seasonal prefix when butler_name is not provided
    assert call["prompt"] == "Do something"


async def test_tick_job_mode_no_seasonal_injection(scheduler_pool):
    """tick() does not modify job-mode dispatches (prompt is not involved)."""
    from butlers.core.scheduler import tick
    from butlers.core.seasonal import seasonal_period_create

    butler = "tick-butler4"

    await scheduler_pool.execute("""
        INSERT INTO scheduled_tasks (name, cron, dispatch_mode, job_name, next_run_at)
        VALUES ('job-task', '* * * * *', 'job', 'some_job', now() - interval '1 minute')
    """)

    await seasonal_period_create(
        scheduler_pool,
        butler,
        "year-round",
        "annual",
        start_month=1,
        start_day=1,
        end_month=12,
        end_day=31,
    )

    dispatch = _Dispatch()
    await tick(scheduler_pool, dispatch, butler_name=butler)

    assert len(dispatch.calls) == 1
    call = dispatch.calls[0]
    # Job-mode dispatch does not include a prompt field
    assert "prompt" not in call
