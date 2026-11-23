"""sw_085: read indexes for the goal ledger dashboard routes (bu-85bqeq.4).

Real PostgreSQL. With ``enable_seqscan`` off the assertion is about index usability, not tiny-table
cost estimates: the ``GET /api/goals`` keyset page is served in order from ``ix_goals_created_id``
(no Sort), and the ``region`` filter probes ``ix_effects_region_goal``. The downgrade drops both.
"""

import asyncio
import shutil
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest

from alembic import command
from butlers.api.routers.goals import _SUMMARY_SELECT
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


def test_the_goal_list_page_is_served_in_order_from_the_created_index(ledger_url) -> None:
    plan = asyncio.run(
        _plan(
            ledger_url,
            # The router's own statement, so the test cannot drift from what runs.
            f"""{_SUMMARY_SELECT}
        WHERE g.exposed = $1 AND (g.created_at, g.id) < ($2, $3)
        ORDER BY g.created_at DESC, g.id DESC LIMIT 51
        """,
            True,
            datetime(2026, 11, 1, tzinfo=UTC),
            uuid4(),
        )
    )
    assert _GOALS_INDEX in plan, plan
    # The page itself is not sorted (the region subquery's own ORDER BY may be).
    assert "Sort" not in plan.splitlines()[1], plan


def test_the_region_filter_probes_the_region_index(ledger_url) -> None:
    plan = asyncio.run(
        _plan(
            ledger_url,
            """
        SELECT 1 FROM switchboard.effects e
        WHERE e.goal_id = $1 AND split_part(e.capability, '__', 1) = $2
        """,
            uuid4(),
            "finance",
        )
    )
    assert _EFFECTS_INDEX in plan, plan


def test_the_downgrade_drops_both_indexes(ledger_url) -> None:
    assert {_GOALS_INDEX, _EFFECTS_INDEX} <= asyncio.run(_index_names(ledger_url))
    config = _build_alembic_config(ledger_url, ["switchboard"], target_schema="switchboard")
    command.downgrade(config, "switchboard@sw_084")  # pinned-revision: sw_085 fence
    assert not {_GOALS_INDEX, _EFFECTS_INDEX} & asyncio.run(_index_names(ledger_url))
