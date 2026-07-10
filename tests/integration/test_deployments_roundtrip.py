"""Real-Postgres regression: the deployments ledger (bu-9r3hd.2).

Exercises core_163 against a fully migrated Postgres instance (testcontainers)
-- not just the mocked-pool unit tests (see tests/core/test_deployments.py for
the AsyncMock-pool coverage of the same module, mirroring the split used for
tests/integration/test_delegation_ledger_roundtrip.py vs
tests/core/test_delegation_ledger.py):

- public.deployments is created with the expected columns and the `result`
  CHECK constraint enforces the success/failed vocabulary.
- record_deployment / get_current_deployment / list_recent_deployments
  round-trip through the real table via the actual production writer/reader
  in butlers.core.deployments.
"""

from __future__ import annotations

import shutil

import asyncpg
import pytest

from butlers.core.deployments import (
    get_current_deployment,
    list_recent_deployments,
    record_deployment,
)
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------


async def test_deployments_table_exists_with_expected_columns(pool: asyncpg.Pool) -> None:
    rows = await pool.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'deployments'
        """
    )
    columns = {r["column_name"] for r in rows}
    assert columns == {
        "id",
        "git_sha",
        "migration_head",
        "started_at",
        "finished_at",
        "result",
    }


async def test_result_check_constraint_rejects_bogus_value(pool: asyncpg.Pool) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO public.deployments (git_sha, result)
            VALUES ('abc1234', 'in_progress')
            """
        )


async def test_git_sha_is_required(pool: asyncpg.Pool) -> None:
    with pytest.raises(asyncpg.NotNullViolationError):
        await pool.execute("INSERT INTO public.deployments (result) VALUES ('success')")


# ---------------------------------------------------------------------------
# record_deployment / get_current_deployment / list_recent_deployments round
# trip via the real production writer+reader.
# ---------------------------------------------------------------------------


async def test_record_and_read_current_deployment(pool: asyncpg.Pool) -> None:
    row_id = await record_deployment(
        pool, git_sha="abc1234", migration_head="core_163", result="success"
    )
    assert row_id is not None

    current = await get_current_deployment(pool)
    assert current is not None
    assert current["git_sha"] == "abc1234"
    assert current["migration_head"] == "core_163"
    assert current["result"] == "success"
    assert current["started_at"] is not None
    assert current["finished_at"] is not None


async def test_current_deployment_is_the_most_recent_row(pool: asyncpg.Pool) -> None:
    await record_deployment(pool, git_sha="older", migration_head="core_162", result="success")
    await record_deployment(pool, git_sha="newer", migration_head="core_163", result="success")

    current = await get_current_deployment(pool)
    assert current["git_sha"] == "newer"


async def test_list_recent_deployments_orders_newest_first(pool: asyncpg.Pool) -> None:
    await record_deployment(pool, git_sha="sha-a", migration_head="core_163", result="success")
    await record_deployment(pool, git_sha="sha-b", migration_head="core_163", result="failed")

    recent = await list_recent_deployments(pool, limit=2)
    assert len(recent) == 2
    assert recent[0]["git_sha"] == "sha-b"
    assert recent[1]["git_sha"] == "sha-a"


async def test_failed_deploy_and_null_migration_head_persist_honestly(pool: asyncpg.Pool) -> None:
    """A boot with no readable alembic_version and a partial start must record
    the honest degraded state, never a fabricated success."""
    row_id = await record_deployment(
        pool, git_sha="broken-sha", migration_head=None, result="failed"
    )
    row = await pool.fetchrow(
        "SELECT git_sha, migration_head, result FROM public.deployments WHERE id = $1", row_id
    )
    assert row["git_sha"] == "broken-sha"
    assert row["migration_head"] is None
    assert row["result"] == "failed"
