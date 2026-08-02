"""Real-Postgres regression: infra-state QA discovery views (bu-9r3hd.4).

Exercises migrations ``sw_024`` / ``sw_028`` (``public.v_qa_connector_state`` /
``public.v_qa_butler_heartbeat``) against a fully migrated Postgres instance
(testcontainers), not just the mocked-pool unit tests in
``tests/core/qa/test_infra_state.py``:

- Both views exist and are queryable.
- ``v_qa_connector_state`` surfaces live and registered ``connector_registry``
  rows while excluding soft-deleted, archived, and storage-only checkpoint rows.
- ``v_qa_butler_heartbeat`` surfaces a ``butler_registry`` row with its
  ``liveness_ttl_seconds`` / ``quarantined_at`` columns intact.
- Downgrade cleanly drops both views.

Uses ``schemas={"switchboard": "switchboard"}`` (rather than leaving both
chains unmapped into ``public``) to prove the ACTUAL deployed shape works:
``connector_registry`` / ``butler_registry`` live under a schema literally
named ``switchboard`` (mirroring cli.py's ``_migrate_all``, which passes
``schema=config.db_schema`` for the switchboard butler's own chain run), and
the migration's unqualified ``FROM connector_registry`` must resolve via
that chain's own search_path rather than accidentally binding to some other
schema's same-named table.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)
    yield p
    await p.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_both_views_exist_and_are_queryable(pool: asyncpg.Pool) -> None:
    await pool.execute("SELECT 1 FROM public.v_qa_connector_state LIMIT 0")
    await pool.execute("SELECT 1 FROM public.v_qa_butler_heartbeat LIMIT 0")


@pytest.mark.asyncio(loop_scope="session")
async def test_connector_view_surfaces_live_row_and_excludes_non_liveness_rows(
    pool: asyncpg.Pool,
) -> None:
    now = datetime.now(UTC)
    await pool.execute(
        """
        INSERT INTO switchboard.connector_registry
            (connector_type, endpoint_identity, state, last_heartbeat_at, first_seen_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        "gmail",
        "owner@example.com",
        "error",
        now - timedelta(minutes=20),
        now - timedelta(days=5),
    )
    await pool.execute(
        """
        INSERT INTO switchboard.connector_registry
            (connector_type, endpoint_identity, state, last_heartbeat_at,
             first_seen_at, archived_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        "spotify",
        "owner",
        "error",
        now - timedelta(days=90),
        now - timedelta(days=100),
        now - timedelta(days=40),
    )
    await pool.execute(
        """
        INSERT INTO switchboard.connector_registry
            (connector_type, endpoint_identity, state, last_heartbeat_at,
             first_seen_at, deleted_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        "owntracks",
        "owner",
        "error",
        now - timedelta(days=90),
        now - timedelta(days=100),
        now - timedelta(days=40),
    )
    await pool.execute(
        """
        INSERT INTO switchboard.connector_registry
            (connector_type, endpoint_identity, checkpoint_cursor,
             checkpoint_updated_at, first_seen_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        "google_health",
        "google_health:user:owner@example.com:account-id:hrv",
        "cursor-value",
        now - timedelta(minutes=5),
        now - timedelta(days=5),
    )
    await pool.execute(
        """
        INSERT INTO switchboard.connector_registry
            (connector_type, endpoint_identity, instance_id, checkpoint_cursor,
             checkpoint_updated_at, first_seen_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        "telegram_bot",
        "owner",
        "11111111-1111-1111-1111-111111111111",
        "cursor-value",
        now - timedelta(minutes=5),
        now - timedelta(minutes=5),
    )
    await pool.execute(
        """
        INSERT INTO switchboard.connector_registry
            (connector_type, endpoint_identity, registered_via, first_seen_at)
        VALUES ($1, $2, $3, $4)
        """,
        "webhook",
        "owner",
        "operator",
        now - timedelta(minutes=5),
    )

    rows = await pool.fetch("SELECT * FROM public.v_qa_connector_state ORDER BY connector_type")

    assert [r["connector_type"] for r in rows] == ["gmail", "telegram_bot", "webhook"]
    row = rows[0]
    assert row["endpoint_identity"] == "owner@example.com"
    assert row["state"] == "error"
    assert row["last_heartbeat_at"] is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_heartbeat_view_surfaces_registry_row(pool: asyncpg.Pool) -> None:
    now = datetime.now(UTC)
    await pool.execute(
        """
        INSERT INTO switchboard.butler_registry
            (name, endpoint_url, last_seen_at, liveness_ttl_seconds, quarantined_at,
             quarantine_reason)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (name) DO UPDATE SET
            last_seen_at = EXCLUDED.last_seen_at,
            liveness_ttl_seconds = EXCLUDED.liveness_ttl_seconds,
            quarantined_at = EXCLUDED.quarantined_at,
            quarantine_reason = EXCLUDED.quarantine_reason
        """,
        "finance",
        "http://finance:41100/sse",
        now - timedelta(hours=1),
        300,
        now - timedelta(minutes=30),
        "no heartbeat in ttl window",
    )

    row = await pool.fetchrow(
        "SELECT * FROM public.v_qa_butler_heartbeat WHERE name = $1", "finance"
    )

    assert row is not None
    assert row["liveness_ttl_seconds"] == 300
    assert row["quarantined_at"] is not None
    assert row["last_seen_at"] is not None


def test_downgrade_drops_both_views(postgres_container) -> None:
    """Mirrors test_switchboard_spot_check_index_migration.py's downgrade test shape:
    a standalone DB (not the shared module fixture) so the downgrade never
    affects the other tests in this module.
    """
    from alembic import command
    from butlers.migrations import _build_alembic_config

    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )

    config = _build_alembic_config(db_url, chains=["switchboard"], target_schema="switchboard")
    command.downgrade(config, "switchboard@sw_023")

    async def _assert_views_gone() -> None:
        p = await asyncpg.create_pool(db_url, min_size=1, max_size=1)
        try:
            row = await p.fetchrow(
                "SELECT to_regclass('public.v_qa_connector_state') AS connector_view, "
                "to_regclass('public.v_qa_butler_heartbeat') AS heartbeat_view"
            )
            assert row["connector_view"] is None
            assert row["heartbeat_view"] is None
        finally:
            await p.close()

    import asyncio

    asyncio.run(_assert_views_gone())
