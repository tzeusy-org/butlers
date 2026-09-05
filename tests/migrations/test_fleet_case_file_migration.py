"""Regression tests for core_217 (bu-8cdl1.7 Slice 1 — the Fleet Case File).

Mirrors tests/migrations/test_owner_conditions_migration.py: the migration
must work on a fresh/core-only database AND an existing multi-schema
database, must survive being applied a second time (all DDL is guarded with
IF NOT EXISTS/DO-block existence checks), and the switchboard-only write
restriction on fleet_cases/fleet_case_links must actually be enforced by RLS,
not merely by GRANT/REVOKE.

Constraint/shape tests use the container's privileged bootstrap login (which
bypasses RLS, like any Postgres superuser) so they exercise CHECK/UNIQUE/FK
behavior without also fighting the switchboard-only write policy; the RLS
enforcement itself is exercised separately against the ordinary migration
login with ``SET ROLE``.
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

from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_217_fleet_case_file.py"
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]
_asyncio_session = pytest.mark.asyncio(loop_scope="session")


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_217", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def _fresh_core_only_db_name() -> str:
    return migration_db_name()


@pytest.fixture(scope="module")
def fresh_core_only_db_url(postgres_container, _fresh_core_only_db_name: str) -> str:
    return create_migrated_test_db(postgres_container, _fresh_core_only_db_name, chains=["core"])


@pytest.fixture(scope="module")
def fresh_core_only_bootstrap_url(
    postgres_container, _fresh_core_only_db_name: str, fresh_core_only_db_url: str
) -> str:
    # asyncpg only accepts the "postgresql://"/"postgres://" schemes; the
    # bootstrap URL comes back in SQLAlchemy's "postgresql+psycopg2://" form.
    return migration_bootstrap_db_url(postgres_container, _fresh_core_only_db_name).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )


@pytest.fixture(scope="module")
def multi_schema_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory", "relationship"],
        schemas={"relationship": "relationship"},
    )


def _columns(db_url: str, table: str) -> set[str]:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :table"
            ),
            {"table": table},
        )
        columns = {row[0] for row in rows}
    engine.dispose()
    return columns


_EXPECTED_CASE_COLUMNS = {
    "id",
    "correlation_key",
    "state",
    "posture",
    "outcome",
    "opened_at",
    "updated_at",
    "closed_at",
}
_EXPECTED_EVIDENCE_COLUMNS = {
    "id",
    "case_id",
    "contributor",
    "kind",
    "ref",
    "payload",
    "contributed_at",
}
_EXPECTED_LINK_COLUMNS = {
    "id",
    "case_id",
    "link_kind",
    "ref",
    "metadata",
    "linked_at",
}


@_asyncio_session
async def test_tables_exist_with_expected_columns_on_fresh_core_only_db(
    fresh_core_only_db_url: str,
) -> None:
    assert _columns(fresh_core_only_db_url, "fleet_cases") == _EXPECTED_CASE_COLUMNS
    assert _columns(fresh_core_only_db_url, "fleet_case_evidence") == _EXPECTED_EVIDENCE_COLUMNS
    assert _columns(fresh_core_only_db_url, "fleet_case_links") == _EXPECTED_LINK_COLUMNS


@_asyncio_session
async def test_tables_exist_with_expected_columns_on_multi_schema_db(
    multi_schema_db_url: str,
) -> None:
    assert _columns(multi_schema_db_url, "fleet_cases") == _EXPECTED_CASE_COLUMNS
    assert _columns(multi_schema_db_url, "fleet_case_evidence") == _EXPECTED_EVIDENCE_COLUMNS
    assert _columns(multi_schema_db_url, "fleet_case_links") == _EXPECTED_LINK_COLUMNS


def _index_names(db_url: str, table: str) -> set[str]:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = :table"
            ),
            {"table": table},
        )
        names = {row[0] for row in rows}
    engine.dispose()
    return names


@_asyncio_session
async def test_expected_indexes_exist(fresh_core_only_db_url: str) -> None:
    case_indexes = _index_names(fresh_core_only_db_url, "fleet_cases")
    assert "uq_fleet_cases_active_correlation_key" in case_indexes
    assert "idx_fleet_cases_state_updated" in case_indexes

    evidence_indexes = _index_names(fresh_core_only_db_url, "fleet_case_evidence")
    assert "uq_fleet_case_evidence_contributor" in evidence_indexes
    assert "idx_fleet_case_evidence_case_id" in evidence_indexes

    link_indexes = _index_names(fresh_core_only_db_url, "fleet_case_links")
    assert "uq_fleet_case_links_ref" in link_indexes
    assert "idx_fleet_case_links_case_id" in link_indexes


async def _bootstrap_pool(bootstrap_url: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(bootstrap_url, min_size=1, max_size=1)


@_asyncio_session
async def test_state_check_constraint_rejects_bogus_value(
    fresh_core_only_bootstrap_url: str,
) -> None:
    pool = await _bootstrap_pool(fresh_core_only_bootstrap_url)
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                """
                INSERT INTO public.fleet_cases (correlation_key, state)
                VALUES ('test:bogus-state', 'bogus')
                """
            )
    finally:
        await pool.close()


@_asyncio_session
async def test_posture_check_constraint_rejects_bogus_value(
    fresh_core_only_bootstrap_url: str,
) -> None:
    pool = await _bootstrap_pool(fresh_core_only_bootstrap_url)
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                """
                INSERT INTO public.fleet_cases (correlation_key, posture)
                VALUES ('test:bogus-posture', 'bogus')
                """
            )
    finally:
        await pool.close()


@_asyncio_session
async def test_closed_requires_outcome(fresh_core_only_bootstrap_url: str) -> None:
    pool = await _bootstrap_pool(fresh_core_only_bootstrap_url)
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                """
                INSERT INTO public.fleet_cases (correlation_key, state, closed_at)
                VALUES ('test:closed-no-outcome', 'closed', now())
                """
            )
    finally:
        await pool.close()


@_asyncio_session
async def test_non_closed_state_rejects_outcome(fresh_core_only_bootstrap_url: str) -> None:
    pool = await _bootstrap_pool(fresh_core_only_bootstrap_url)
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                """
                INSERT INTO public.fleet_cases (correlation_key, state, outcome)
                VALUES ('test:open-with-outcome', 'open', 'resolved')
                """
            )
    finally:
        await pool.close()


@_asyncio_session
async def test_close_with_outcome_and_closed_at_succeeds(
    fresh_core_only_bootstrap_url: str,
) -> None:
    pool = await _bootstrap_pool(fresh_core_only_bootstrap_url)
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO public.fleet_cases (correlation_key, state, outcome, closed_at)
            VALUES ('test:closed-with-outcome', 'closed', 'resolved', now())
            RETURNING id
            """
        )
        assert row is not None
    finally:
        await pool.execute(
            "DELETE FROM public.fleet_cases WHERE correlation_key = 'test:closed-with-outcome'"
        )
        await pool.close()


@_asyncio_session
async def test_active_correlation_key_uniqueness_rejects_second_active_case(
    fresh_core_only_bootstrap_url: str,
) -> None:
    pool = await _bootstrap_pool(fresh_core_only_bootstrap_url)
    try:
        await pool.execute(
            """
            INSERT INTO public.fleet_cases (correlation_key, state)
            VALUES ('test:dup-key', 'open')
            """
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await pool.execute(
                """
                INSERT INTO public.fleet_cases (correlation_key, state)
                VALUES ('test:dup-key', 'watching')
                """
            )
    finally:
        await pool.execute("DELETE FROM public.fleet_cases WHERE correlation_key = 'test:dup-key'")
        await pool.close()


@_asyncio_session
async def test_closed_case_does_not_block_reopening_same_correlation_key(
    fresh_core_only_bootstrap_url: str,
) -> None:
    pool = await _bootstrap_pool(fresh_core_only_bootstrap_url)
    try:
        await pool.execute(
            """
            INSERT INTO public.fleet_cases (correlation_key, state, outcome, closed_at)
            VALUES ('test:reopen-key', 'closed', 'resolved', now())
            """
        )
        # A second, still-active case for the same key is allowed once the
        # first is closed — the partial unique index only guards active rows.
        row = await pool.fetchrow(
            """
            INSERT INTO public.fleet_cases (correlation_key, state)
            VALUES ('test:reopen-key', 'open')
            RETURNING id
            """
        )
        assert row is not None
    finally:
        await pool.execute(
            "DELETE FROM public.fleet_cases WHERE correlation_key = 'test:reopen-key'"
        )
        await pool.close()


@_asyncio_session
async def test_evidence_idempotent_per_contributor_kind_ref(
    fresh_core_only_bootstrap_url: str,
) -> None:
    pool = await _bootstrap_pool(fresh_core_only_bootstrap_url)
    try:
        case_row = await pool.fetchrow(
            """
            INSERT INTO public.fleet_cases (correlation_key, state)
            VALUES ('test:evidence-case', 'open')
            RETURNING id
            """
        )
        case_id = case_row["id"]

        await pool.execute(
            """
            INSERT INTO public.fleet_case_evidence (case_id, contributor, kind, ref)
            VALUES ($1, 'butler_health_rw', 'candidate', 'ref-1')
            """,
            case_id,
        )
        # Same contributor, same (kind, ref) again: idempotent — the unique
        # constraint refuses a second row rather than silently duplicating.
        with pytest.raises(asyncpg.UniqueViolationError):
            await pool.execute(
                """
                INSERT INTO public.fleet_case_evidence (case_id, contributor, kind, ref)
                VALUES ($1, 'butler_health_rw', 'candidate', 'ref-1')
                """,
                case_id,
            )
        # A different contributor reporting the same (kind, ref) is a
        # distinct, allowed row.
        other_row = await pool.fetchrow(
            """
            INSERT INTO public.fleet_case_evidence (case_id, contributor, kind, ref)
            VALUES ($1, 'butler_relationship_rw', 'candidate', 'ref-1')
            RETURNING id
            """,
            case_id,
        )
        assert other_row is not None

        count = await pool.fetchval(
            "SELECT count(*) FROM public.fleet_case_evidence WHERE case_id = $1", case_id
        )
        assert count == 2
    finally:
        await pool.execute(
            "DELETE FROM public.fleet_cases WHERE correlation_key = 'test:evidence-case'"
        )
        await pool.close()


@_asyncio_session
async def test_evidence_cascades_on_case_delete(fresh_core_only_bootstrap_url: str) -> None:
    pool = await _bootstrap_pool(fresh_core_only_bootstrap_url)
    try:
        case_row = await pool.fetchrow(
            """
            INSERT INTO public.fleet_cases (correlation_key, state)
            VALUES ('test:cascade-case', 'open')
            RETURNING id
            """
        )
        case_id = case_row["id"]
        await pool.execute(
            """
            INSERT INTO public.fleet_case_evidence (case_id, contributor, kind, ref)
            VALUES ($1, 'butler_health_rw', 'candidate', 'ref-1')
            """,
            case_id,
        )
        await pool.execute("DELETE FROM public.fleet_cases WHERE id = $1", case_id)
        count = await pool.fetchval(
            "SELECT count(*) FROM public.fleet_case_evidence WHERE case_id = $1", case_id
        )
        assert count == 0
    finally:
        await pool.close()


@_asyncio_session
async def test_switchboard_role_can_insert_case_other_roles_cannot(
    fresh_core_only_db_url: str, fresh_core_only_bootstrap_url: str
) -> None:
    bootstrap_pool = await _bootstrap_pool(fresh_core_only_bootstrap_url)
    try:
        switchboard_conn = await asyncpg.connect(fresh_core_only_db_url)
        try:
            await switchboard_conn.execute("SET ROLE butler_switchboard_rw")
            row = await switchboard_conn.fetchrow(
                """
                INSERT INTO public.fleet_cases (correlation_key, state)
                VALUES ('test:switchboard-writes', 'open')
                RETURNING id
                """
            )
            assert row is not None
        finally:
            await switchboard_conn.close()

        other_conn = await asyncpg.connect(fresh_core_only_db_url)
        try:
            await other_conn.execute("SET ROLE butler_health_rw")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await other_conn.execute(
                    """
                    INSERT INTO public.fleet_cases (correlation_key, state)
                    VALUES ('test:health-cannot-write', 'open')
                    """
                )
        finally:
            await other_conn.close()
    finally:
        await bootstrap_pool.execute(
            "DELETE FROM public.fleet_cases WHERE correlation_key = 'test:switchboard-writes'"
        )
        await bootstrap_pool.close()


@_asyncio_session
async def test_any_role_can_insert_evidence(
    fresh_core_only_db_url: str, fresh_core_only_bootstrap_url: str
) -> None:
    bootstrap_pool = await _bootstrap_pool(fresh_core_only_bootstrap_url)
    try:
        case_row = await bootstrap_pool.fetchrow(
            """
            INSERT INTO public.fleet_cases (correlation_key, state)
            VALUES ('test:any-role-evidence', 'open')
            RETURNING id
            """
        )
        case_id = case_row["id"]

        health_conn = await asyncpg.connect(fresh_core_only_db_url)
        try:
            await health_conn.execute("SET ROLE butler_health_rw")
            row = await health_conn.fetchrow(
                """
                INSERT INTO public.fleet_case_evidence (case_id, contributor, kind, ref)
                VALUES ($1, 'butler_health_rw', 'candidate', 'ref-role-check')
                RETURNING id
                """,
                case_id,
            )
            assert row is not None
        finally:
            await health_conn.close()
    finally:
        await bootstrap_pool.execute(
            "DELETE FROM public.fleet_cases WHERE correlation_key = 'test:any-role-evidence'"
        )
        await bootstrap_pool.close()


def test_upgrade_is_idempotent_when_applied_a_second_time(fresh_core_only_db_url: str) -> None:
    module = _load_migration()
    engine = create_engine(fresh_core_only_db_url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        real_op = Operations(ctx)
        with patch.object(module, "op", real_op):
            module.upgrade()
    engine.dispose()

    assert _columns(fresh_core_only_db_url, "fleet_cases") == _EXPECTED_CASE_COLUMNS
    assert _columns(fresh_core_only_db_url, "fleet_case_evidence") == _EXPECTED_EVIDENCE_COLUMNS
    assert _columns(fresh_core_only_db_url, "fleet_case_links") == _EXPECTED_LINK_COLUMNS
