"""Regression tests for core_184 (bu-ep4ks.6 — the owner_conditions ledger).

Mirrors tests/migrations/test_infra_conditions_migration.py: the migration
must work on a fresh/core-only database AND an existing multi-schema
database, and must survive being applied a second time (all DDL is guarded
with IF NOT EXISTS/DO-block existence checks).
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
    / "core_184_owner_conditions.py"
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]
_asyncio_session = pytest.mark.asyncio(loop_scope="session")


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_184", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def fresh_core_only_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture(scope="module")
def multi_schema_db_url(postgres_container) -> str:
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
                "WHERE table_schema = 'public' AND table_name = 'owner_conditions'"
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
                "WHERE schemaname = 'public' AND tablename = 'owner_conditions'"
            )
        )
        names = {row[0] for row in rows}
    engine.dispose()
    return names


@_asyncio_session
async def test_expected_indexes_exist(fresh_core_only_db_url: str) -> None:
    names = _index_names(fresh_core_only_db_url)
    assert "uq_owner_conditions_active_episode" in names
    assert "uq_owner_conditions_identity_episode" in names
    assert "idx_owner_conditions_due" in names
    assert "idx_owner_conditions_source_state" in names


@_asyncio_session
async def test_state_check_constraint_rejects_bogus_value(fresh_core_only_db_url: str) -> None:
    pool = await asyncpg.create_pool(fresh_core_only_db_url, min_size=1, max_size=1)
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                """
                INSERT INTO public.owner_conditions (source, fingerprint, episode, state)
                VALUES ('finance:bill-overdue', 'abc123', 1, 'bogus')
                """
            )
    finally:
        await pool.close()


@_asyncio_session
async def test_active_episode_uniqueness_rejects_a_second_active_row(
    fresh_core_only_db_url: str,
) -> None:
    pool = await asyncpg.create_pool(fresh_core_only_db_url, min_size=1, max_size=1)
    try:
        await pool.execute(
            """
            INSERT INTO public.owner_conditions (source, fingerprint, episode, state)
            VALUES ('finance:bill-overdue', 'dup-fp', 1, 'open')
            """
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await pool.execute(
                """
                INSERT INTO public.owner_conditions (source, fingerprint, episode, state)
                VALUES ('finance:bill-overdue', 'dup-fp', 2, 'aging')
                """
            )
    finally:
        await pool.execute(
            "DELETE FROM public.owner_conditions WHERE source = 'finance:bill-overdue' "
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
                INSERT INTO public.owner_conditions
                    (source, fingerprint, episode, state, resolved_at)
                VALUES ('finance:bill-overdue', 'partial-resolve', 1, 'resolved', now())
                """
            )
    finally:
        await pool.close()


def test_upgrade_is_idempotent_when_applied_a_second_time(fresh_core_only_db_url: str) -> None:
    module = _load_migration()
    engine = create_engine(fresh_core_only_db_url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        real_op = Operations(ctx)
        with patch.object(module, "op", real_op):
            module.upgrade()
    engine.dispose()

    assert _table_columns(fresh_core_only_db_url) == _EXPECTED_COLUMNS
