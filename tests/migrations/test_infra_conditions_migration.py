"""Regression tests for core_182 (bu-27dxl.6.2 — the infra_conditions ledger).

AC5: the migration must work on a fresh/core-only database AND an existing
multi-schema database. AC6: the migration must survive being applied a
second time (all its DDL is guarded with IF NOT EXISTS/DO-block existence
checks).
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from unittest.mock import patch

import asyncpg
import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text

from butlers.testing.migration import create_migrated_test_db, migration_db_name

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_182_infra_conditions.py"
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]
_asyncio_session = pytest.mark.asyncio(loop_scope="session")


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_182", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_chain_linkage() -> None:
    module = _load_migration()
    assert module.revision == "core_182"
    assert module.down_revision == "core_181"
    assert module.branch_labels is None
    assert module.depends_on is None


@pytest.fixture(scope="module")
def fresh_core_only_db_url(postgres_container) -> str:
    """AC5, half 1: a fresh database that only ever runs the core chain."""
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture(scope="module")
def multi_schema_db_url(postgres_container) -> str:
    """AC5, half 2: an existing database with sibling schema-scoped chains already applied."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory", "relationship"],
        schemas={"relationship": "relationship"},
    )


def _table_columns(db_url: str) -> set[str]:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'infra_conditions'"
            )
        )
        columns = {row[0] for row in rows}
    engine.dispose()
    return columns


_EXPECTED_COLUMNS = {
    "id",
    "source",
    "fingerprint",
    "episode",
    "state",
    "first_detected_at",
    "last_confirmed_at",
    "last_escalated_at",
    "next_reescalate_at",
    "escalation_level",
    "resolved_at",
    "recovered_after_s",
    "summary",
    "metadata",
}


@_asyncio_session
async def test_table_exists_with_expected_columns_on_fresh_core_only_db(
    fresh_core_only_db_url: str,
) -> None:
    assert _table_columns(fresh_core_only_db_url) == _EXPECTED_COLUMNS


@_asyncio_session
async def test_table_exists_with_expected_columns_on_multi_schema_db(
    multi_schema_db_url: str,
) -> None:
    assert _table_columns(multi_schema_db_url) == _EXPECTED_COLUMNS


def _index_names(db_url: str) -> set[str]:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = 'infra_conditions'"
            )
        )
        names = {row[0] for row in rows}
    engine.dispose()
    return names


@_asyncio_session
async def test_expected_indexes_exist(fresh_core_only_db_url: str) -> None:
    names = _index_names(fresh_core_only_db_url)
    assert "uq_infra_conditions_active_episode" in names
    assert "uq_infra_conditions_identity_episode" in names
    assert "idx_infra_conditions_due" in names
    assert "idx_infra_conditions_source_state" in names


@_asyncio_session
async def test_state_check_constraint_rejects_bogus_value(fresh_core_only_db_url: str) -> None:
    pool = await asyncpg.create_pool(fresh_core_only_db_url, min_size=1, max_size=1)
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                """
                INSERT INTO public.infra_conditions (source, fingerprint, episode, state)
                VALUES ('deploy_drift', 'abc123', 1, 'bogus')
                """
            )
    finally:
        await pool.close()


@_asyncio_session
async def test_active_episode_uniqueness_rejects_a_second_active_row(
    fresh_core_only_db_url: str,
) -> None:
    """Two 'open' rows for the same (source, fingerprint) violate the partial unique index."""
    pool = await asyncpg.create_pool(fresh_core_only_db_url, min_size=1, max_size=1)
    try:
        await pool.execute(
            """
            INSERT INTO public.infra_conditions (source, fingerprint, episode, state)
            VALUES ('deploy_drift', 'dup-fp', 1, 'open')
            """
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await pool.execute(
                """
                INSERT INTO public.infra_conditions (source, fingerprint, episode, state)
                VALUES ('deploy_drift', 'dup-fp', 2, 'aging')
                """
            )
    finally:
        await pool.execute(
            "DELETE FROM public.infra_conditions WHERE source = 'deploy_drift' "
            "AND fingerprint = 'dup-fp'"
        )
        await pool.close()


@_asyncio_session
async def test_resolved_fields_check_constraint_requires_both_or_neither(
    fresh_core_only_db_url: str,
) -> None:
    pool = await asyncpg.create_pool(fresh_core_only_db_url, min_size=1, max_size=1)
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                """
                INSERT INTO public.infra_conditions
                    (source, fingerprint, episode, state, resolved_at)
                VALUES ('deploy_drift', 'partial-resolve', 1, 'resolved', now())
                """
            )
    finally:
        await pool.close()


def test_upgrade_is_idempotent_when_applied_a_second_time(fresh_core_only_db_url: str) -> None:
    """AC6: replay this migration's own upgrade() a second time against a DB
    already at core_182 head — every statement is IF NOT EXISTS/DO-block
    guarded, so this must succeed without error."""
    module = _load_migration()
    engine = create_engine(fresh_core_only_db_url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        real_op = Operations(ctx)
        with patch.object(module, "op", real_op):
            module.upgrade()
    engine.dispose()

    assert _table_columns(fresh_core_only_db_url) == _EXPECTED_COLUMNS
