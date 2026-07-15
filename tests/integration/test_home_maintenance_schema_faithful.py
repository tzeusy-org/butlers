"""Real-Postgres proof that ``run_maintenance_schedule_check`` reads
``home.maintenance_items`` schema-qualified, not via search_path (bu-4rif7).

Closes a mock-only coverage gap: ``tests/jobs/test_home.py`` exercises this
read through a hand-rolled mock pool only, so an accidental un-qualification of
``FROM home.maintenance_items`` would pass unit tests but break in production
(the #2598 mocked-green / integration-red class). This test provisions the home
chain into a real ``home`` schema and drives the job through a pool whose
``search_path`` is ``public`` only (the home tables are NOT on the path), so the
read resolves *solely* because it is qualified. Un-qualify it and this test
raises ``UndefinedTableError``.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.jobs.home import run_maintenance_schedule_check
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def home_db_url(postgres_container) -> str:
    # Faithful topology: core into public, the home domain chain into its own
    # ``home`` schema (mirrors lifecycle.py provisioning the home butler).
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "home"],
        schemas={"core": "public", "home": "home"},
    )


@pytest.fixture
async def public_pool(home_db_url: str) -> asyncpg.Pool:
    """Pool scoped to ``public`` only — the ``home`` schema is NOT on the path,
    so ``home.maintenance_items`` resolves only via its qualification."""
    p = await asyncpg.create_pool(
        home_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "public"},
    )
    yield p
    await p.close()


async def _seed_item(
    pool: asyncpg.Pool,
    *,
    name: str,
    category: str = "filter",
    interval_days: int = 90,
    next_due_at: datetime | None,
    last_completed_at: datetime | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO home.maintenance_items
            (name, category, interval_days, next_due_at, last_completed_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        name,
        category,
        interval_days,
        next_due_at,
        last_completed_at,
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_maintenance_check_reads_home_schema_under_public_search_path(
    public_pool: asyncpg.Pool,
) -> None:
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)

    # Sanity: the public-only pool genuinely cannot see the table unqualified.
    with pytest.raises(asyncpg.UndefinedTableError):
        await public_pool.fetch("SELECT id FROM maintenance_items")

    # One overdue, one upcoming, one never-completed -> all three are selected.
    await _seed_item(public_pool, name="overdue filter", next_due_at=now - timedelta(days=10))
    await _seed_item(
        public_pool, name="upcoming hvac", category="hvac", next_due_at=now + timedelta(days=3)
    )
    await _seed_item(public_pool, name="never started", next_due_at=None)

    captured: list[str] = []

    async def notify(text: str) -> None:
        captured.append(text)

    result = await run_maintenance_schedule_check(public_pool, None, notify_fn=notify, _now=now)

    # teeth: the read resolves only because it is schema-qualified to home.*
    assert result["items_checked"] == 3
    assert result["reminders_sent"] == 1
    assert captured, "a reminder should have been produced for the due/overdue items"
