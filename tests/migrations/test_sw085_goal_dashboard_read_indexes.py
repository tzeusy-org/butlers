"""sw_085: read indexes for the goal ledger dashboard routes (bu-85bqeq.4).

Real PostgreSQL. With ``enable_seqscan`` off the assertion is about index usability, not tiny-table
cost estimates: the ``GET /api/goals`` keyset page is served in order from ``ix_goals_created_id``
(no Sort), and the ``region`` filter probes ``ix_effects_region_goal``. The downgrade drops both.
"""

import asyncio
import shutil
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import asyncpg
import pytest

from alembic import command
from butlers.api.pagination import encode_cursor
from butlers.api.routers.goals import CURSOR_SCOPE, digest, list_goals
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_GOALS_INDEX = "ix_goals_created_id"
_EFFECTS_INDEX = "ix_effects_region_goal"


@pytest.fixture
def ledger_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        revisions={"switchboard": "sw_085"},  # pinned-revision: owns the read indexes
    )


async def _plan(url: str, sql: str, *args) -> str:
    conn = await asyncpg.connect(url)
    try:
        # The tables are nearly empty, so cost estimates would pick a bitmap scan plus a Sort.
        # Disabling those asks the real question: CAN the index serve the ordered page.
        for setting in ("enable_seqscan", "enable_bitmapscan", "enable_sort"):
            await conn.execute(f"SET {setting} = off")
        rows = await conn.fetch(f"EXPLAIN {sql}", *args)
    finally:
        await conn.close()
    return "\n".join(r[0] for r in rows)


async def _index_names(url: str) -> set[str]:
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch("SELECT indexname FROM pg_indexes WHERE schemaname = 'switchboard'")
    finally:
        await conn.close()
    return {r["indexname"] for r in rows}


async def _list_plan(url: str, **filters) -> str:
    """Capture the real route builder, including its EXISTS and bound limit."""
    pool = AsyncMock()
    pool.fetch.return_value = []
    db = MagicMock()
    db.pool.return_value = pool
    options = dict(
        origin=None,
        exposed=None,
        state=None,
        region=None,
        outcome=None,
        since=None,
        until=None,
        cursor=None,
        limit=50,
    )
    options.update(filters)
    await list_goals(db=db, **options)
    sql, *args = pool.fetch.await_args.args
    return await _plan(url, sql, *args)


def test_the_goal_list_page_is_served_in_order_from_the_created_index(ledger_url) -> None:
    plan = asyncio.run(
        _list_plan(
            ledger_url,
            exposed=True,
            cursor=encode_cursor(
                (
                    datetime(2026, 11, 1, tzinfo=UTC),
                    uuid4(),
                    digest([None, True, None, None, None, None, None]),
                ),
                scope=CURSOR_SCOPE,
            ),
        )
    )
    assert _GOALS_INDEX in plan, plan
    # The page itself is not sorted (the region subquery's own ORDER BY may be).
    assert "Sort" not in plan.splitlines()[1], plan


def test_the_region_filter_probes_the_region_index(ledger_url) -> None:
    plan = asyncio.run(
        _list_plan(
            ledger_url,
            region="finance",
        )
    )
    assert _EFFECTS_INDEX in plan, plan
    # The projection's region array also reads effects. Require the filtered
    # EXISTS probe itself to constrain the indexed prefix, not merely name an index.
    assert any(
        "Index Cond:" in line and "split_part" in line and "'finance'" in line
        for line in plan.splitlines()
    ), plan


def test_the_downgrade_drops_both_indexes(ledger_url) -> None:
    assert {_GOALS_INDEX, _EFFECTS_INDEX} <= asyncio.run(_index_names(ledger_url))
    config = _build_alembic_config(ledger_url, ["switchboard"], target_schema="switchboard")
    command.downgrade(config, "switchboard@sw_084")  # pinned-revision: sw_085 fence
    assert not {_GOALS_INDEX, _EFFECTS_INDEX} & asyncio.run(_index_names(ledger_url))
