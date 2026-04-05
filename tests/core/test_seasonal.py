"""Integration tests for butlers.core.seasonal — condensed.

Covers:
- get_active_seasons: same-year, cross-year, disabled, multiple concurrent
- seasonal_period CRUD: create, update, list, delete with validation
- seasonal_period_create_preset: presets and unknown raises
- tick() seasonal context injection
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
# get_active_seasons
# ---------------------------------------------------------------------------


async def test_get_active_seasons_filtering(pool):
    """get_active_seasons returns in-range periods; excludes disabled and out-of-range."""
    from butlers.core.seasonal import get_active_seasons, seasonal_period_create

    butler = f"butler-{uuid.uuid4().hex[:8]}"

    # Same-year period: Jan 1 - Apr 15
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
    # Cross-year period: Nov 15 - Jan 10
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
    # Disabled period: always active range
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

    # Jan 5: tax-season active, winter active (cross-year: Nov 15–Jan 10), disabled excluded
    active = await get_active_seasons(pool, butler, today=date(2025, 1, 5))
    names = {p["name"] for p in active}
    assert "tax-season" in names
    assert "winter" in names
    assert "disabled" not in names

    # Jan 20: tax-season still active (Jan 20 < Apr 15), winter ended (Jan 20 > Jan 10)
    active_jan20 = await get_active_seasons(pool, butler, today=date(2025, 1, 20))
    names_jan20 = {p["name"] for p in active_jan20}
    assert "tax-season" in names_jan20
    assert "winter" not in names_jan20

    # June: neither active
    active_june = await get_active_seasons(pool, butler, today=date(2025, 6, 1))
    assert len(active_june) == 0


# ---------------------------------------------------------------------------
# seasonal_period_create
# ---------------------------------------------------------------------------


async def test_seasonal_period_create_and_validation(pool):
    """create returns UUID; duplicate name raises; invalid dates raise; different butlers ok."""
    from butlers.core.seasonal import seasonal_period_create

    butler = f"butler-{uuid.uuid4().hex[:8]}"
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

    # Duplicate name same butler raises
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

    # Different butler same name is OK
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


# ---------------------------------------------------------------------------
# seasonal_period_update / list / delete
# ---------------------------------------------------------------------------


async def test_seasonal_period_update(pool):
    """update changes enabled flag and dates; not-found returns False."""
    from butlers.core.seasonal import (
        get_active_seasons,
        seasonal_period_create,
        seasonal_period_update,
    )

    butler = f"butler-{uuid.uuid4().hex[:8]}"
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

    # Active before disable
    assert len(await get_active_seasons(pool, butler, today=date(2025, 6, 15))) == 1

    # Disable it
    await seasonal_period_update(pool, butler, period_id, enabled=False)
    assert len(await get_active_seasons(pool, butler, today=date(2025, 6, 15))) == 0

    # Not-found returns False
    result = await seasonal_period_update(pool, butler, uuid.uuid4(), enabled=True)
    assert result is False


async def test_seasonal_period_list_and_delete(pool):
    """list includes is_active field; delete removes and returns True; not-found returns False."""
    from butlers.core.seasonal import (
        seasonal_period_create,
        seasonal_period_delete,
        seasonal_period_list,
    )

    butler = f"butler-{uuid.uuid4().hex[:8]}"
    period_id = await seasonal_period_create(
        pool,
        butler,
        "delete-me",
        "annual",
        start_month=3,
        start_day=1,
        end_month=5,
        end_day=31,
    )

    periods = await seasonal_period_list(pool, butler)
    assert len(periods) == 1
    assert "is_active" in periods[0]

    # Delete it
    result = await seasonal_period_delete(pool, butler, period_id)
    assert result is True
    assert len(await seasonal_period_list(pool, butler)) == 0

    # Not-found returns False
    assert await seasonal_period_delete(pool, butler, uuid.uuid4()) is False


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


async def test_seasonal_period_presets(pool):
    """Presets create correctly; unknown preset raises ValueError."""
    from butlers.core.seasonal import (
        seasonal_period_create_preset,
        seasonal_period_list,
    )

    butler = f"butler-{uuid.uuid4().hex[:8]}"
    await seasonal_period_create_preset(pool, butler, "us-tax-season")
    periods = await seasonal_period_list(pool, butler)
    assert len(periods) == 1
    assert periods[0]["name"] == "us-tax-season"

    with pytest.raises(ValueError, match="[Uu]nknown preset"):
        await seasonal_period_create_preset(pool, butler, "space-christmas")


# ---------------------------------------------------------------------------
# tick() seasonal context injection
# ---------------------------------------------------------------------------


async def test_tick_injects_active_seasons(scheduler_pool):
    """tick() prepends seasonal context to prompt when active periods exist."""
    from butlers.core.scheduler import tick
    from butlers.core.seasonal import seasonal_period_create

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

    class _Dispatch:
        calls: list = []

        async def __call__(self, **kwargs):
            self.calls.append(kwargs)

    d = _Dispatch()
    await tick(scheduler_pool, d, butler_name=butler)

    assert len(d.calls) == 1
    prompt = d.calls[0]["prompt"]
    assert "year-round" in prompt
    assert "Seasonal context" in prompt
    assert "Do something" in prompt


async def test_tick_no_active_seasons_prompt_unchanged(scheduler_pool):
    """tick() leaves prompt unchanged when no active seasons."""
    from butlers.core.scheduler import tick

    butler = f"butler-{uuid.uuid4().hex[:8]}"
    await scheduler_pool.execute(
        """
        INSERT INTO scheduled_tasks (name, cron, dispatch_mode, prompt, next_run_at)
        VALUES ($1, '* * * * *', 'prompt', 'Do something', now() - interval '1 minute')
        """,
        f"task-{uuid.uuid4().hex[:8]}",
    )

    class _Dispatch:
        calls: list = []

        async def __call__(self, **kwargs):
            self.calls.append(kwargs)

    d = _Dispatch()
    await tick(scheduler_pool, d, butler_name=butler)

    assert d.calls[0]["prompt"] == "Do something"
