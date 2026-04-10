"""Tests for butlers.core.seasonal — unit and integration — condensed.

Covers:
- validate_month_day: boundary validation
- _is_date_in_range: same-year, cross-year, boundary cases
- SEASONAL_PRESETS: keys, valid dates, context_hints
- get_active_seasons: same-year, cross-year, disabled, multiple concurrent
- seasonal_period CRUD: create, update, list, delete with validation
- seasonal_period_create_preset: presets and unknown raises
- tick() seasonal context injection and no-active-seasons passthrough
"""

from __future__ import annotations

import shutil
import uuid
from datetime import date

import asyncpg
import pytest

docker_available = shutil.which("docker") is not None

# ---------------------------------------------------------------------------
# Unit tests — no DB required
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_month_day() -> None:
    """Valid month/day pairs accepted; invalid ones raise ValueError."""
    from butlers.core.seasonal import validate_month_day

    for m, d in [(1, 1), (1, 31), (2, 28), (4, 30), (12, 31), (11, 30)]:
        validate_month_day(m, d)  # should not raise

    invalid_cases = [
        (2, 30, "February"),
        (2, 29, "February"),
        (4, 31, "April"),
        (11, 31, "November"),
        (0, 1, "month"),
        (13, 1, "month"),
        (3, 0, "day"),
        (5, -1, "day"),
    ]
    for month, day, match in invalid_cases:
        with pytest.raises(ValueError, match=match):
            validate_month_day(month, day)


@pytest.mark.unit
@pytest.mark.parametrize(
    "month,day,sm,sd,em,ed,expected",
    [
        # Same-year (Jan 1 – Apr 15)
        (3, 15, 1, 1, 4, 15, True),  # mid season
        (1, 1, 1, 1, 4, 15, True),  # start boundary
        (4, 15, 1, 1, 4, 15, True),  # end boundary
        (6, 1, 1, 1, 4, 15, False),  # outside
        # Cross-year (Nov 15 – Jan 10)
        (12, 20, 11, 15, 1, 10, True),  # December
        (1, 5, 11, 15, 1, 10, True),  # January
        (11, 15, 11, 15, 1, 10, True),  # start boundary
        (1, 10, 11, 15, 1, 10, True),  # end boundary
        (6, 15, 11, 15, 1, 10, False),  # mid-year
        (1, 11, 11, 15, 1, 10, False),  # just past end
        (11, 14, 11, 15, 1, 10, False),  # just before start
    ],
)
def test_is_date_in_range(month, day, sm, sd, em, ed, expected) -> None:
    from butlers.core.seasonal import _is_date_in_range

    assert _is_date_in_range(month, day, sm, sd, em, ed) == expected


@pytest.mark.unit
def test_seasonal_presets() -> None:
    """Presets have correct keys, valid dates, context_hints, and year-boundary wrapping."""
    from butlers.core.seasonal import SEASONAL_PRESETS, _is_date_in_range, validate_month_day

    expected = {
        "us-tax-season",
        "year-end-holidays",
        "back-to-school",
        "spring-semester",
        "fall-semester",
    }
    assert set(SEASONAL_PRESETS.keys()) == expected

    for name, preset in SEASONAL_PRESETS.items():
        validate_month_day(preset["start_month"], preset["start_day"])
        validate_month_day(preset["end_month"], preset["end_day"])
        hint = preset.get("metadata", {}).get("context_hint", "").strip()
        assert hint, f"Preset {name!r} missing context_hint"

    tax = SEASONAL_PRESETS["us-tax-season"]
    assert (tax["start_month"], tax["start_day"]) == (1, 1)
    assert (tax["end_month"], tax["end_day"]) == (4, 15)

    ye = SEASONAL_PRESETS["year-end-holidays"]
    sm, sd, em, ed = ye["start_month"], ye["start_day"], ye["end_month"], ye["end_day"]
    assert _is_date_in_range(12, 25, sm, sd, em, ed)
    assert not _is_date_in_range(3, 1, sm, sd, em, ed)


# ---------------------------------------------------------------------------
# Integration fixtures
# ---------------------------------------------------------------------------

pytestmark_int = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


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

    p = await asyncpg.create_pool(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database=db_name,
        min_size=1,
        max_size=3,
    )
    for ddl in ddl_statements:
        await p.execute(ddl)
    return p


@pytest.fixture
async def pool(postgres_container):
    p = await _make_pool(postgres_container, _SEASONAL_TABLE_DDL)
    yield p
    await p.close()


@pytest.fixture
async def scheduler_pool(postgres_container):
    p = await _make_pool(postgres_container, _SCHEDULED_TASKS_DDL, _SEASONAL_TABLE_DDL)
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_get_active_seasons_filtering(pool):
    """get_active_seasons returns in-range enabled periods; excludes disabled and out-of-range."""
    from butlers.core.seasonal import get_active_seasons, seasonal_period_create

    butler = f"butler-{uuid.uuid4().hex[:8]}"
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
        "winter",
        "annual",
        start_month=11,
        start_day=15,
        end_month=1,
        end_day=10,
    )
    await seasonal_period_create(
        pool,
        butler,
        "disabled",
        "annual",
        start_month=1,
        start_day=1,
        end_month=12,
        end_day=31,
        enabled=False,
    )

    # Jan 5: tax-season + winter active (cross-year), disabled excluded
    active = await get_active_seasons(pool, butler, today=date(2025, 1, 5))
    names = {p["name"] for p in active}
    assert "tax-season" in names and "winter" in names and "disabled" not in names

    # Jan 20: tax-season active, winter ended
    active_jan20 = await get_active_seasons(pool, butler, today=date(2025, 1, 20))
    names2 = {p["name"] for p in active_jan20}
    assert "tax-season" in names2 and "winter" not in names2

    # June: none active
    assert len(await get_active_seasons(pool, butler, today=date(2025, 6, 1))) == 0


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_seasonal_period_crud(pool):
    """create/update/list/delete operations; validation; preset creation."""
    from butlers.core.seasonal import (
        get_active_seasons,
        seasonal_period_create,
        seasonal_period_create_preset,
        seasonal_period_delete,
        seasonal_period_list,
        seasonal_period_update,
    )

    butler = f"butler-{uuid.uuid4().hex[:8]}"

    # Create
    period_id = await seasonal_period_create(
        pool,
        butler,
        "my-season",
        "annual",
        start_month=1,
        start_day=1,
        end_month=3,
        end_day=31,
    )
    assert period_id is not None

    # Duplicate same butler raises
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

    # Different butler same name OK
    id2 = await seasonal_period_create(
        pool,
        "other-butler",
        "my-season",
        "annual",
        start_month=1,
        start_day=1,
        end_month=3,
        end_day=31,
    )
    assert id2 != period_id

    # Invalid date raises
    with pytest.raises(ValueError, match="February"):
        await seasonal_period_create(
            pool,
            "b-b",
            "bad",
            "annual",
            start_month=2,
            start_day=30,
            end_month=3,
            end_day=15,
        )

    # Update (enable/disable)
    butler2 = f"butler-{uuid.uuid4().hex[:8]}"
    year_round_id = await seasonal_period_create(
        pool,
        butler2,
        "year-round",
        "annual",
        start_month=1,
        start_day=1,
        end_month=12,
        end_day=31,
    )
    assert len(await get_active_seasons(pool, butler2, today=date(2025, 6, 15))) == 1
    await seasonal_period_update(pool, butler2, year_round_id, enabled=False)
    assert len(await get_active_seasons(pool, butler2, today=date(2025, 6, 15))) == 0
    result = await seasonal_period_update(pool, butler2, uuid.uuid4(), enabled=True)
    assert result is False

    # List includes is_active field
    butler3 = f"butler-{uuid.uuid4().hex[:8]}"
    del_id = await seasonal_period_create(
        pool,
        butler3,
        "delete-me",
        "annual",
        start_month=3,
        start_day=1,
        end_month=5,
        end_day=31,
    )
    periods = await seasonal_period_list(pool, butler3)
    assert len(periods) == 1 and "is_active" in periods[0]

    # Delete
    assert await seasonal_period_delete(pool, butler3, del_id) is True
    assert len(await seasonal_period_list(pool, butler3)) == 0
    assert await seasonal_period_delete(pool, butler3, uuid.uuid4()) is False

    # Preset creation
    butler4 = f"butler-{uuid.uuid4().hex[:8]}"
    await seasonal_period_create_preset(pool, butler4, "us-tax-season")
    p4 = await seasonal_period_list(pool, butler4)
    assert len(p4) == 1 and p4[0]["name"] == "us-tax-season"

    with pytest.raises(ValueError, match="[Uu]nknown preset"):
        await seasonal_period_create_preset(pool, butler4, "space-christmas")


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_tick_seasonal_context_injection(scheduler_pool):
    """tick() prepends seasonal context when active; leaves prompt unchanged when no seasons."""
    from butlers.core.scheduler import tick
    from butlers.core.seasonal import seasonal_period_create

    class _Dispatch:
        calls: list = []

        async def __call__(self, **kwargs):
            self.calls.append(kwargs)

    # With active season
    butler = f"butler-{uuid.uuid4().hex[:8]}"
    await scheduler_pool.execute(
        """
        INSERT INTO scheduled_tasks (name, cron, dispatch_mode, prompt, next_run_at)
        VALUES ($1, '* * * * *', 'prompt', 'Do something', now() - interval '1 minute')
        """,
        f"task-{uuid.uuid4().hex[:8]}",
    )
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
    d = _Dispatch()
    d.calls = []
    await tick(scheduler_pool, d, butler_name=butler)
    assert len(d.calls) == 1
    prompt = d.calls[0]["prompt"]
    assert "year-round" in prompt and "Seasonal context" in prompt and "Do something" in prompt

    # Without active seasons — prompt unchanged
    butler2 = f"butler-{uuid.uuid4().hex[:8]}"
    await scheduler_pool.execute(
        """
        INSERT INTO scheduled_tasks (name, cron, dispatch_mode, prompt, next_run_at)
        VALUES ($1, '* * * * *', 'prompt', 'Do something', now() - interval '1 minute')
        """,
        f"task-{uuid.uuid4().hex[:8]}",
    )
    d2 = _Dispatch()
    d2.calls = []
    await tick(scheduler_pool, d2, butler_name=butler2)
    assert d2.calls[0]["prompt"] == "Do something"
