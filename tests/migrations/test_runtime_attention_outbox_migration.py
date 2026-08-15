"""Real-Postgres contract tests for the inert runtime-attention outbox.

REQ-runtime-attention-outbox-001 defines durable, safe episode representation
without migration-time paging or historical backfill.  REQ-database-security-007
requires effective-role (rather than source-only) proof that producers can only
perform validated appends while Switchboard owns delivery transitions.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import select
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import (
    create_migration_db,
    init_db_sql_for_dbapi,
    migration_bootstrap_db_url,
    migration_db_name,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.db,
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_OUTBOX = "public.runtime_attention_outbox"
_MODEL_PRODUCER = "butler_general_rw"
_SWITCHBOARD = "butler_switchboard_rw"
_CONNECTOR = "connector_writer"
_FUNCTION_MODEL_BREAKER = "public.append_runtime_attention_model_breaker(bigint)"
_FUNCTION_FLEET_HALT = "public.append_runtime_attention_fleet_halt()"
_CORE_198_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_198_runtime_attention_outbox.py"
)


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _upgrade_to_core_197(db_url: str) -> None:
    command.upgrade(_build_alembic_config(db_url, chains=["core"]), "core_197")


def _upgrade_to_core_head(db_url: str) -> None:
    command.upgrade(_build_alembic_config(db_url, chains=["core"]), "core@head")


def _load_core_198_migration():
    """Load the migration so catalog proof uses its canonical predicate."""
    spec = importlib.util.spec_from_file_location(
        "core_198_runtime_attention_outbox", _CORE_198_MIGRATION
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _has_finalized_runtime_attention_interface(db_url: str, proof_sql: str) -> bool:
    """Evaluate one caller-specific finalized ACL/catalog predicate."""
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return bool(conn.execute(text(proof_sql)).scalar_one())
    finally:
        engine.dispose()


def _has_exact_finalized_runtime_attention_interface(db_url: str) -> bool:
    """Evaluate the ordinary migration-role finalized predicate."""
    return _has_finalized_runtime_attention_interface(
        db_url, _load_core_198_migration()._TRUSTED_FINALIZED_INTERFACE_SQL
    )


def _has_bootstrap_finalized_runtime_attention_interface(db_url: str) -> bool:
    """Evaluate the managed-bootstrap finalized predicate."""
    return _has_finalized_runtime_attention_interface(
        db_url, _load_core_198_migration()._TRUSTED_BOOTSTRAP_FINALIZED_INTERFACE_SQL
    )


_CORE_HEAD_UPGRADE_PROCESS = """
import os
import sys
import traceback

from alembic import command
from butlers.migrations import _build_alembic_config

db_url, target_schema, ready_fd, release_fd = sys.argv[1:]
try:
    config = _build_alembic_config(db_url, chains=["core"], target_schema=target_schema)
    os.write(int(ready_fd), b"1")
    if os.read(int(release_fd), 1) != b"1":
        raise RuntimeError("concurrent core-upgrade barrier was not released")
    command.upgrade(config, "core@head")
except BaseException:
    traceback.print_exc()
    raise
"""


_CORE_197_DOWNGRADE_PROCESS = """
import os
import sys
import traceback

from alembic import command
from butlers.migrations import _build_alembic_config

db_url, target_schema, ready_fd, release_fd = sys.argv[1:]
try:
    config = _build_alembic_config(db_url, chains=["core"], target_schema=target_schema)
    os.write(int(ready_fd), b"1")
    if os.read(int(release_fd), 1) != b"1":
        raise RuntimeError("concurrent core-downgrade barrier was not released")
    command.downgrade(config, "core_197")
except BaseException:
    traceback.print_exc()
    raise
"""


def _run_concurrent_core_head_upgrades(
    db_url: str, target_schemas: tuple[str, str]
) -> dict[str, tuple[int, str, str]]:
    """Release two fresh Python processes together at the core_198 boundary."""
    ready_read, ready_write = os.pipe()
    release_read, release_write = os.pipe()
    processes: dict[str, subprocess.Popen[str]] = {}
    try:
        for target_schema in target_schemas:
            processes[target_schema] = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    _CORE_HEAD_UPGRADE_PROCESS,
                    db_url,
                    target_schema,
                    str(ready_write),
                    str(release_read),
                ],
                cwd=Path(__file__).resolve().parents[2],
                pass_fds=(ready_write, release_read),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        os.close(ready_write)
        ready_write = -1
        os.close(release_read)
        release_read = -1

        ready = b""
        while len(ready) < len(target_schemas):
            readable, _, _ = select.select([ready_read], [], [], 45)
            if not readable:
                raise TimeoutError("concurrent core-upgrade processes did not reach the barrier")
            chunk = os.read(ready_read, len(target_schemas) - len(ready))
            if not chunk:
                raise RuntimeError("concurrent core-upgrade process exited before the barrier")
            ready += chunk
        assert ready == b"1" * len(target_schemas)
        assert os.write(release_write, b"1" * len(target_schemas)) == len(target_schemas)
        os.close(release_write)
        release_write = -1

        results: dict[str, tuple[int, str, str]] = {}
        for target_schema, process in processes.items():
            stdout, stderr = process.communicate(timeout=45)
            assert process.returncode is not None
            results[target_schema] = (process.returncode, stdout, stderr)
        return results
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        for process in processes.values():
            if process.poll() is None:
                process.communicate(timeout=5)
        for file_descriptor in (ready_read, ready_write, release_read, release_write):
            if file_descriptor >= 0:
                os.close(file_descriptor)


def _run_concurrent_core_197_downgrades(
    db_url: str, target_schemas: tuple[str, str]
) -> dict[str, tuple[int, str, str]]:
    """Release two bootstrap downgrades together at the core_198 boundary."""
    ready_read, ready_write = os.pipe()
    release_read, release_write = os.pipe()
    processes: dict[str, subprocess.Popen[str]] = {}
    try:
        for target_schema in target_schemas:
            processes[target_schema] = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    _CORE_197_DOWNGRADE_PROCESS,
                    db_url,
                    target_schema,
                    str(ready_write),
                    str(release_read),
                ],
                cwd=Path(__file__).resolve().parents[2],
                pass_fds=(ready_write, release_read),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        os.close(ready_write)
        ready_write = -1
        os.close(release_read)
        release_read = -1

        ready = b""
        while len(ready) < len(target_schemas):
            readable, _, _ = select.select([ready_read], [], [], 45)
            if not readable:
                raise TimeoutError("concurrent core-downgrade processes did not reach the barrier")
            chunk = os.read(ready_read, len(target_schemas) - len(ready))
            if not chunk:
                raise RuntimeError("concurrent core-downgrade process exited before the barrier")
            ready += chunk
        assert ready == b"1" * len(target_schemas)
        assert os.write(release_write, b"1" * len(target_schemas)) == len(target_schemas)
        os.close(release_write)
        release_write = -1

        results: dict[str, tuple[int, str, str]] = {}
        for target_schema, process in processes.items():
            stdout, stderr = process.communicate(timeout=45)
            assert process.returncode is not None
            results[target_schema] = (process.returncode, stdout, stderr)
        return results
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        for process in processes.values():
            if process.poll() is None:
                process.communicate(timeout=5)
        for file_descriptor in (ready_read, ready_write, release_read, release_write):
            if file_descriptor >= 0:
                os.close(file_descriptor)


def _rerun_actual_init_db(bootstrap_url: str, migration_url: str) -> None:
    """Execute the checked-in bootstrap source again, as production does.

    This is intentionally not a hand-written ACL approximation: the broad
    public grants in init-db must run first so the finalizer proves it repairs
    them on every rerun.
    """
    migration_role = unquote(urlparse(migration_url).username or "")
    assert migration_role
    engine = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    raw_connection = engine.raw_connection()
    try:
        raw_connection.autocommit = True
        with raw_connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('butlers.connecting_user', %s, false)",
                (migration_role,),
            )
            cursor.execute(init_db_sql_for_dbapi())
    finally:
        raw_connection.close()
        engine.dispose()


async def _decode_jsonb(connection: asyncpg.Connection) -> None:
    await connection.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


def _seed_legacy_breaker_edge(db_url: str) -> tuple[uuid.UUID, int]:
    """Create an already-open edge before the representation migration runs."""
    entry_id = uuid.uuid4()
    now = datetime.now(UTC)
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO public.model_catalog (id, alias, runtime_type, model_id)
                    VALUES (:id, 'runtime-attention-legacy', 'codex', 'legacy-model')
                    """
                ),
                {"id": entry_id},
            )
            trigger_id: int | None = None
            for offset in range(5):
                trigger_id = conn.execute(
                    text(
                        """
                        INSERT INTO public.model_dispatch_attempts (
                            catalog_entry_id, ts, butler, outcome, error_code, error_message
                        )
                        VALUES (:catalog_entry_id, :ts, 'general', 'runtime_failure',
                                'RuntimeFailure', 'provider detail must never enter the outbox')
                        RETURNING id
                        """
                    ),
                    {
                        "catalog_entry_id": entry_id,
                        "ts": now - timedelta(minutes=5 - offset),
                    },
                ).scalar_one()
    finally:
        engine.dispose()
    assert trigger_id is not None
    return entry_id, trigger_id


@pytest.fixture(scope="module")
def upgraded_db_url(postgres_container) -> str:
    """An upgraded database proves the migration does not page old incidents."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    _upgrade_to_core_197(db_url)
    _seed_legacy_breaker_edge(db_url)
    _upgrade_to_core_head(db_url)
    return db_url


@pytest.fixture(scope="module")
def core_only_db_url(postgres_container) -> str:
    """A fresh core-only database has no specialist-schema prerequisites."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core"))
    return db_url


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def upgraded_pool(upgraded_db_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(upgraded_db_url, min_size=1, max_size=3, init=_decode_jsonb)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def core_only_pool(core_only_db_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(core_only_db_url, min_size=1, max_size=2, init=_decode_jsonb)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def core_only_admin_pool(postgres_container, core_only_db_url: str) -> asyncpg.Pool:
    db_name = urlparse(core_only_db_url).path.lstrip("/")
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )
    pool = await asyncpg.create_pool(bootstrap_url, min_size=1, max_size=2, init=_decode_jsonb)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def upgraded_admin_pool(postgres_container, upgraded_db_url: str) -> asyncpg.Pool:
    db_name = urlparse(upgraded_db_url).path.lstrip("/")
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )
    pool = await asyncpg.create_pool(
        bootstrap_url,
        min_size=1,
        max_size=2,
        init=_decode_jsonb,
    )
    yield pool
    await pool.close()


async def _call_as_role(
    pool: asyncpg.Pool,
    role: str,
    statement: str,
    *args: object,
) -> object:
    async with pool.acquire() as conn:
        await conn.execute(f"SET ROLE {_quote_ident(role)}")
        try:
            return await conn.fetchval(statement, *args)
        finally:
            await conn.execute("RESET ROLE")


async def _execute_as_role(
    pool: asyncpg.Pool,
    role: str,
    statement: str,
    *args: object,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(f"SET ROLE {_quote_ident(role)}")
        try:
            await conn.execute(statement, *args)
        finally:
            await conn.execute("RESET ROLE")


async def _fetchrow_as_role(
    pool: asyncpg.Pool,
    role: str,
    statement: str,
    *args: object,
) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        await conn.execute(f"SET ROLE {_quote_ident(role)}")
        try:
            return await conn.fetchrow(statement, *args)
        finally:
            await conn.execute("RESET ROLE")


async def _seed_breaker_edge(pool: asyncpg.Pool, alias: str) -> tuple[uuid.UUID, int]:
    entry_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.model_catalog (id, alias, runtime_type, model_id)
            VALUES ($1, $2, 'codex', 'runtime-attention-test-model')
            """,
            entry_id,
            alias,
        )
        trigger_id: int | None = None
        for offset in range(5):
            trigger_id = await conn.fetchval(
                """
                INSERT INTO public.model_dispatch_attempts (
                    catalog_entry_id, ts, butler, outcome, error_code, error_message
                )
                VALUES ($1, $2, 'general', 'runtime_failure', 'RuntimeFailure',
                        'unsafe provider detail must not be copied')
                RETURNING id
                """,
                entry_id,
                now - timedelta(minutes=5 - offset),
            )
    assert isinstance(trigger_id, int)
    return entry_id, trigger_id


async def _seed_fleet_halt_evidence(pool: asyncpg.Pool) -> None:
    entry_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.model_catalog (id, alias, runtime_type, model_id)
            VALUES ($1, 'runtime-attention-fleet-halt', 'codex', 'fleet-halt-model')
            """,
            entry_id,
        )
        await conn.execute(
            """
            INSERT INTO public.model_dispatch_attempts (
                catalog_entry_id, ts, butler, outcome, failure_reason
            )
            VALUES ($1, now(), 'general', 'quota_skip',
                    'Monthly spend ceiling reached: test evidence')
            """,
            entry_id,
        )


@pytest.mark.asyncio(loop_scope="module")
async def test_upgraded_database_creates_no_historical_runtime_attention_episode(
    upgraded_pool: asyncpg.Pool,
    upgraded_admin_pool: asyncpg.Pool,
) -> None:
    """REQ-runtime-attention-outbox-001: migration itself never backfills/pages."""
    assert (
        await upgraded_pool.fetchval(f"SELECT to_regclass('{_OUTBOX}')")
        == "runtime_attention_outbox"
    )
    assert await upgraded_admin_pool.fetchval(f"SELECT count(*) FROM {_OUTBOX}") == 0


@pytest.mark.asyncio(loop_scope="module")
async def test_core_only_database_has_guarded_outbox_without_specialist_schema(
    core_only_pool: asyncpg.Pool,
    core_only_admin_pool: asyncpg.Pool,
) -> None:
    """REQ-runtime-attention-outbox-001: core-only deployment remains migratable."""
    assert (
        await core_only_pool.fetchval(f"SELECT to_regclass('{_OUTBOX}')")
        == "runtime_attention_outbox"
    )
    assert await core_only_pool.fetchval("SELECT to_regclass('relationship.entity_facts')") is None
    assert await core_only_pool.fetchval("SELECT to_regclass('switchboard.notifications')") is None
    assert await core_only_pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM pg_indexes
            WHERE schemaname = 'public'
              AND indexname = 'idx_model_dispatch_attempts_catalog_ts_id'
        )
        """
    )
    assert await core_only_pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM pg_indexes
            WHERE schemaname = 'public'
              AND indexname = 'idx_model_dispatch_attempts_outcome_ts_id'
        )
        """
    )
    acl_shape = await core_only_admin_pool.fetchrow(
        """
        SELECT
            owner_role.rolcanlogin = false
                AND owner_role.rolinherit = false
                AND owner_role.rolsuper = false
                AND owner_role.rolbypassrls = false AS constrained_owner,
            model_breaker.prosecdef
                AND model_breaker.proconfig = ARRAY[
                    'search_path=pg_catalog, public, pg_temp'
                ]::text[] AS fixed_definer_path,
            fleet_halt.prosecdef
                AND fleet_halt.proconfig = ARRAY[
                    'search_path=pg_catalog, public, pg_temp'
                ]::text[] AS fixed_fleet_path,
            lease_guard.prosecdef
                AND lease_guard.proconfig = ARRAY[
                    'search_path=pg_catalog, public, pg_temp'
                ]::text[] AS fixed_lease_guard_path,
            has_table_privilege(owner_role.oid, 'public.model_catalog'::regclass, 'SELECT')
                AND has_table_privilege(
                    owner_role.oid, 'public.model_dispatch_attempts'::regclass, 'SELECT'
                )
                AND NOT has_table_privilege(
                    owner_role.oid, 'public.model_catalog'::regclass, 'INSERT'
                )
                AND NOT has_table_privilege(
                    owner_role.oid, 'public.model_dispatch_attempts'::regclass, 'UPDATE'
                )
                AND NOT has_table_privilege(
                    owner_role.oid, 'public.model_catalog'::regclass, 'DELETE'
                )
                AND NOT has_table_privilege(
                    owner_role.oid, 'public.model_dispatch_attempts'::regclass, 'DELETE'
                ) AS source_read_only,
            NOT EXISTS (
                SELECT 1
                FROM aclexplode(COALESCE(model_breaker.proacl, acldefault('f', model_breaker.proowner)))
                    AS acl
                WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
            )
                AND NOT has_function_privilege(
                    'connector_writer'::regrole,
                    'public.append_runtime_attention_model_breaker(bigint)'::regprocedure,
                    'EXECUTE'
                )
                AND NOT has_function_privilege(
                    'connector_writer'::regrole,
                    'public.append_runtime_attention_fleet_halt()'::regprocedure,
                    'EXECUTE'
                ) AS no_public_or_connector_execute,
            NOT EXISTS (
                SELECT 1
                FROM aclexplode(
                    COALESCE(admin_schema.nspacl, acldefault('n', admin_schema.nspowner))
                ) AS acl
                WHERE acl.grantee <> bootstrap_owner.oid
            )
                AND NOT EXISTS (
                    SELECT 1
                    FROM aclexplode(
                        COALESCE(
                            bootstrap_configuration.relacl,
                            acldefault('r', bootstrap_configuration.relowner)
                        )
                    ) AS acl
                    WHERE acl.grantee <> bootstrap_owner.oid
                )
                AND NOT EXISTS (
                    SELECT 1
                    FROM pg_proc AS admin_function
                    CROSS JOIN LATERAL aclexplode(
                        COALESCE(
                            admin_function.proacl,
                            acldefault('f', admin_function.proowner)
                        )
                    ) AS acl
                    WHERE admin_function.oid IN (
                        'runtime_attention_admin.install_interface()'::regprocedure,
                        'runtime_attention_admin.finalize_interface()'::regprocedure,
                        'runtime_attention_admin.rollback_interface()'::regprocedure
                    )
                      AND acl.privilege_type = 'EXECUTE'
                      AND acl.grantee <> bootstrap_owner.oid
                ) AS bootstrap_interface_private
        FROM pg_roles AS owner_role
        JOIN pg_proc AS model_breaker
          ON model_breaker.oid = 'public.append_runtime_attention_model_breaker(bigint)'::regprocedure
        JOIN pg_proc AS fleet_halt
          ON fleet_halt.oid = 'public.append_runtime_attention_fleet_halt()'::regprocedure
        JOIN pg_proc AS lease_guard
          ON lease_guard.oid = 'public.runtime_attention_delivery_lease_guard()'::regprocedure
        JOIN pg_namespace AS admin_schema
          ON admin_schema.nspname = 'runtime_attention_admin'
        JOIN pg_roles AS bootstrap_owner ON bootstrap_owner.oid = admin_schema.nspowner
        JOIN pg_class AS bootstrap_configuration
          ON bootstrap_configuration.relnamespace = admin_schema.oid
         AND bootstrap_configuration.relname = 'bootstrap_configuration'
        WHERE owner_role.rolname = 'runtime_attention_outbox_owner'
        """
    )
    assert acl_shape is not None
    assert all(dict(acl_shape).values())


@pytest.mark.asyncio(loop_scope="module")
async def test_core_only_outbox_stages_bounded_delivery_evidence_without_worker_access(
    core_only_admin_pool: asyncpg.Pool,
) -> None:
    """REQ-runtime-attention-outbox-001: dormant delivery data stays safe and inert."""
    staged_columns = await core_only_admin_pool.fetch(
        """
        SELECT attribute.attname
        FROM pg_attribute AS attribute
        WHERE attribute.attrelid = 'public.runtime_attention_outbox'::regclass
          AND attribute.attname = ANY (
              ARRAY['delivery_error_class', 'delivery_error_detail', 'notification_ref']
          )
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
        ORDER BY attribute.attname
        """
    )
    assert [row["attname"] for row in staged_columns] == [
        "delivery_error_class",
        "delivery_error_detail",
        "notification_ref",
    ]
    # This is deliberately a scalar UUID, not a cross-schema foreign key: a
    # core-only database lacks switchboard.notifications but must remain safe
    # to migrate before any worker is activated.
    staged = await core_only_admin_pool.fetchrow(
        f"""
        INSERT INTO {_OUTBOX} (
            source, fleet_halt_month, source_snapshot, payload,
            lifecycle_state, claim_token, claim_epoch, delivery_lease_epoch,
            claimed_by_instance, claimed_at, claim_expires_at,
            delivery_error_class, delivery_error_detail, notification_ref
        )
        VALUES (
            'fleet_halt', date '2099-01-01',
            jsonb_build_object('month', '2099-01-01', 'denied_count', 1, 'first_denied_at', NULL),
            jsonb_build_object('classification', 'monthly_spend_ceiling', 'door', '/spend'),
            'uncertain', gen_random_uuid(), 1, 1,
            'migration-contract-test', now(), now() + interval '30 seconds',
            'transport_uncertain', 'transport_timeout', gen_random_uuid()
        )
        RETURNING delivery_error_class, delivery_error_detail, notification_ref
        """
    )
    assert staged is not None
    assert dict(staged)["delivery_error_class"] == "transport_uncertain"
    assert dict(staged)["delivery_error_detail"] == "transport_timeout"
    assert isinstance(dict(staged)["notification_ref"], uuid.UUID)
    assert await core_only_admin_pool.fetchval(
        """
        SELECT NOT EXISTS (
            SELECT 1
            FROM pg_constraint AS constraint_row
            WHERE constraint_row.conrelid = 'public.runtime_attention_outbox'::regclass
              AND constraint_row.contype = 'f'
        )
        """
    )

    with pytest.raises(asyncpg.CheckViolationError, match="delivery_evidence"):
        await core_only_admin_pool.execute(
            f"""
            INSERT INTO {_OUTBOX} (
                source, fleet_halt_month, source_snapshot, payload,
                lifecycle_state, claim_token, claim_epoch, delivery_lease_epoch,
                claimed_by_instance, claimed_at, claim_expires_at,
                delivery_error_class, delivery_error_detail
            )
            VALUES (
                'fleet_halt', date '2099-02-01',
                jsonb_build_object('month', '2099-02-01', 'denied_count', 1, 'first_denied_at', NULL),
                jsonb_build_object('classification', 'monthly_spend_ceiling', 'door', '/spend'),
                'uncertain', gen_random_uuid(), 1, 1,
                'migration-contract-test', now(), now() + interval '30 seconds',
                'transport_uncertain', 'provider_secret=must_not_persist'
            )
            """
        )

    with pytest.raises(asyncpg.CheckViolationError, match="delivery_evidence"):
        await core_only_admin_pool.execute(
            f"""
            INSERT INTO {_OUTBOX} (
                source, fleet_halt_month, source_snapshot, payload,
                lifecycle_state, claim_token, claim_epoch, delivery_lease_epoch,
                claimed_by_instance, claimed_at, claim_expires_at,
                delivery_error_class
            )
            VALUES (
                'fleet_halt', date '2099-02-15',
                jsonb_build_object('month', '2099-02-15', 'denied_count', 1, 'first_denied_at', NULL),
                jsonb_build_object('classification', 'monthly_spend_ceiling', 'door', '/spend'),
                'uncertain', gen_random_uuid(), 1, 1,
                'migration-contract-test', now(), now() + interval '30 seconds',
                'transport_uncertain'
            )
            """
        )

    with pytest.raises(asyncpg.CheckViolationError, match="delivery_evidence"):
        await core_only_admin_pool.execute(
            f"""
            INSERT INTO {_OUTBOX} (
                source, fleet_halt_month, source_snapshot, payload, notification_ref
            )
            VALUES (
                'fleet_halt', date '2099-03-01',
                jsonb_build_object('month', '2099-03-01', 'denied_count', 1, 'first_denied_at', NULL),
                jsonb_build_object('classification', 'monthly_spend_ceiling', 'door', '/spend'),
                gen_random_uuid()
            )
            """
        )

    assert await core_only_admin_pool.fetchval(
        """
        SELECT
            NOT has_column_privilege(
                'butler_switchboard_rw'::regrole,
                'public.runtime_attention_outbox'::regclass,
                'delivery_error_class',
                'UPDATE'
            )
            AND NOT has_column_privilege(
                'butler_switchboard_rw'::regrole,
                'public.runtime_attention_outbox'::regclass,
                'delivery_error_detail',
                'UPDATE'
            )
            AND NOT has_column_privilege(
                'butler_switchboard_rw'::regrole,
                'public.runtime_attention_outbox'::regclass,
                'notification_ref',
                'UPDATE'
            )
        """
    )


@pytest.mark.asyncio(loop_scope="module")
async def test_validated_producers_append_only_safe_server_derived_episodes(
    upgraded_pool: asyncpg.Pool,
    upgraded_admin_pool: asyncpg.Pool,
) -> None:
    """REQ-runtime-attention-outbox-001 and REQ-database-security-007."""
    entry_id, trigger_id = await _seed_breaker_edge(upgraded_pool, "runtime-attention-safe")

    episode_id = await _call_as_role(
        upgraded_pool,
        _MODEL_PRODUCER,
        "SELECT public.append_runtime_attention_model_breaker($1)",
        trigger_id,
    )
    assert isinstance(episode_id, uuid.UUID)
    # The triggering-edge uniqueness also makes a recorder retry idempotent.
    assert (
        await _call_as_role(
            upgraded_pool,
            _MODEL_PRODUCER,
            "SELECT public.append_runtime_attention_model_breaker($1)",
            trigger_id,
        )
        == episode_id
    )

    row = await upgraded_admin_pool.fetchrow(
        f"""
        SELECT source, lifecycle_state, triggering_attempt_id, source_snapshot, payload,
               claim_epoch, claim_token, manual_reissue_of,
               delivery_error_class, delivery_error_detail, notification_ref
        FROM {_OUTBOX}
        WHERE id = $1
        """,
        episode_id,
    )
    assert row is not None
    assert dict(row) == {
        "source": "model_breaker",
        "lifecycle_state": "pending",
        "triggering_attempt_id": trigger_id,
        "source_snapshot": {
            "catalog_entry_id": str(entry_id),
            "alias": "runtime-attention-safe",
            "model_id": "runtime-attention-test-model",
            "triggering_attempt_id": trigger_id,
            "consecutive_failures": 5,
        },
        "payload": {
            "classification": "model_breaker_open",
            "consecutive_failures": 5,
            "door": f"/settings/models?highlight={entry_id}",
        },
        "claim_epoch": 0,
        "claim_token": None,
        "manual_reissue_of": None,
        "delivery_error_class": None,
        "delivery_error_detail": None,
        "notification_ref": None,
    }
    assert "unsafe provider detail" not in json.dumps(dict(row))

    await _seed_fleet_halt_evidence(upgraded_pool)
    fleet_episode_id = await _call_as_role(
        upgraded_pool,
        _MODEL_PRODUCER,
        "SELECT public.append_runtime_attention_fleet_halt()",
    )
    assert isinstance(fleet_episode_id, uuid.UUID)
    fleet_row = await upgraded_admin_pool.fetchrow(
        f"SELECT source, source_snapshot, payload FROM {_OUTBOX} WHERE id = $1",
        fleet_episode_id,
    )
    assert fleet_row is not None
    assert fleet_row["source"] == "fleet_halt"
    assert fleet_row["source_snapshot"]["denied_count"] >= 1
    assert fleet_row["payload"]["classification"] == "monthly_spend_ceiling"


@pytest.mark.asyncio(loop_scope="module")
async def test_constraints_reject_non_edge_forgery_keep_source_snapshot_and_fence_reissues(
    upgraded_pool: asyncpg.Pool,
    upgraded_admin_pool: asyncpg.Pool,
) -> None:
    """REQ-runtime-attention-outbox-001: constraints preserve durable truth."""
    entry_id = uuid.uuid4()
    async with upgraded_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.model_catalog (id, alias, runtime_type, model_id)
            VALUES ($1, 'runtime-attention-non-edge', 'codex', 'non-edge-model')
            """,
            entry_id,
        )
        non_edge_attempt_id = await conn.fetchval(
            """
            INSERT INTO public.model_dispatch_attempts (catalog_entry_id, butler, outcome)
            VALUES ($1, 'general', 'runtime_failure')
            RETURNING id
            """,
            entry_id,
        )
    assert isinstance(non_edge_attempt_id, int)
    with pytest.raises(asyncpg.CheckViolationError, match="breaker edge"):
        await _call_as_role(
            upgraded_pool,
            _MODEL_PRODUCER,
            "SELECT public.append_runtime_attention_model_breaker($1)",
            non_edge_attempt_id,
        )

    # Even a privileged fixture cannot create an outbox row carrying a raw
    # provider/error sentinel: the durable JSON projection is allowlisted.
    with pytest.raises(asyncpg.CheckViolationError, match="snapshot_allowlist"):
        await upgraded_admin_pool.execute(
            f"""
            INSERT INTO {_OUTBOX} (
                source, triggering_attempt_id, source_snapshot, payload
            )
            VALUES (
                'model_breaker', 999999999,
                $1::jsonb,
                $2::jsonb
            )
            """,
            {
                "catalog_entry_id": "forged",
                "alias": "forged",
                "model_id": "forged",
                "triggering_attempt_id": 999999999,
                "consecutive_failures": 5,
                "error_message": "provider secret sentinel",
            },
            {
                "classification": "model_breaker_open",
                "consecutive_failures": 5,
                "door": "/settings/models?highlight=forged",
            },
        )

    retained_entry_id, retained_trigger_id = await _seed_breaker_edge(
        upgraded_pool, "runtime-attention-retained"
    )
    retained_episode_id = await _call_as_role(
        upgraded_pool,
        _MODEL_PRODUCER,
        "SELECT public.append_runtime_attention_model_breaker($1)",
        retained_trigger_id,
    )
    assert isinstance(retained_episode_id, uuid.UUID)
    await upgraded_admin_pool.execute(
        "DELETE FROM public.model_catalog WHERE id = $1", retained_entry_id
    )
    retained = await upgraded_admin_pool.fetchrow(
        f"SELECT triggering_attempt_id, source_snapshot FROM {_OUTBOX} WHERE id = $1",
        retained_episode_id,
    )
    assert retained is not None
    assert retained["triggering_attempt_id"] == retained_trigger_id
    assert retained["source_snapshot"]["catalog_entry_id"] == str(retained_entry_id)
    with pytest.raises(asyncpg.CheckViolationError, match="snapshots and retention are immutable"):
        await upgraded_admin_pool.execute(
            f"UPDATE {_OUTBOX} SET source_snapshot = '{{}}'::jsonb WHERE id = $1",
            retained_episode_id,
        )
    with pytest.raises(asyncpg.CheckViolationError, match="snapshots and retention are immutable"):
        await upgraded_admin_pool.execute(
            f"UPDATE {_OUTBOX} SET payload = '{{}}'::jsonb WHERE id = $1",
            retained_episode_id,
        )
    with pytest.raises(asyncpg.CheckViolationError, match="snapshots and retention are immutable"):
        await upgraded_admin_pool.execute(
            f"UPDATE {_OUTBOX} "
            "SET retention_until = retention_until + interval '1 day' WHERE id = $1",
            retained_episode_id,
        )
    assert (
        await upgraded_admin_pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM public.model_dispatch_attempts WHERE id = $1)",
            retained_trigger_id,
        )
        is False
    )

    reissue_snapshot = {"reissue_of": str(retained_episode_id)}
    reissue_payload = {"classification": "manual_reissue"}
    await upgraded_admin_pool.execute(
        f"""
        INSERT INTO {_OUTBOX} (
            source, lifecycle_state, source_snapshot, payload, manual_reissue_of
        )
        VALUES ('model_breaker', 'pending', $1::jsonb, $2::jsonb, $3)
        """,
        reissue_snapshot,
        reissue_payload,
        retained_episode_id,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await upgraded_admin_pool.execute(
            f"""
            INSERT INTO {_OUTBOX} (
                source, lifecycle_state, source_snapshot, payload, manual_reissue_of
            )
            VALUES ('model_breaker', 'pending', $1::jsonb, $2::jsonb, $3)
            """,
            reissue_snapshot,
            reissue_payload,
            retained_episode_id,
        )


@pytest.mark.asyncio(loop_scope="module")
async def test_effective_role_boundary_allows_only_validated_producers_and_switchboard_claims(
    upgraded_pool: asyncpg.Pool,
) -> None:
    """REQ-database-security-007: exercise effective PostgreSQL permissions."""
    entry_id, trigger_id = await _seed_breaker_edge(upgraded_pool, "runtime-attention-acl")
    episode_id = await _call_as_role(
        upgraded_pool,
        _MODEL_PRODUCER,
        "SELECT public.append_runtime_attention_model_breaker($1)",
        trigger_id,
    )
    assert isinstance(episode_id, uuid.UUID)

    for role in (_MODEL_PRODUCER, _CONNECTOR):
        for statement in (
            f"SELECT count(*) FROM {_OUTBOX}",
            (
                f"INSERT INTO {_OUTBOX} (source, lifecycle_state, source_snapshot, payload) "
                "VALUES ('model_breaker', 'pending', '{}'::jsonb, '{}'::jsonb)"
            ),
            f"UPDATE {_OUTBOX} SET lifecycle_state = 'failed' WHERE id = '{episode_id}'::uuid",
            f"DELETE FROM {_OUTBOX} WHERE id = '{episode_id}'::uuid",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await _execute_as_role(upgraded_pool, role, statement)

    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _call_as_role(
            upgraded_pool,
            _CONNECTOR,
            "SELECT public.append_runtime_attention_model_breaker($1)",
            trigger_id,
        )

    direct_attempt_id = await upgraded_pool.fetchval(
        "SELECT id FROM public.model_dispatch_attempts WHERE catalog_entry_id = $1 ORDER BY id DESC LIMIT 1",
        entry_id,
    )
    assert isinstance(direct_attempt_id, int)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _call_as_role(
            upgraded_pool,
            _CONNECTOR,
            "SELECT public.append_runtime_attention_model_breaker($1)",
            direct_attempt_id,
        )

    await _execute_as_role(
        upgraded_pool,
        _SWITCHBOARD,
        f"""
        UPDATE {_OUTBOX}
        SET lifecycle_state = 'sending',
            claim_token = gen_random_uuid(),
            claim_epoch = claim_epoch + 1,
            delivery_lease_epoch = 1,
            claimed_by_instance = 'switchboard-test',
            claimed_at = now(),
            claim_expires_at = now() + interval '30 seconds'
        WHERE id = '{episode_id}'::uuid
        """,
    )
    switchboard_state = await _call_as_role(
        upgraded_pool,
        _SWITCHBOARD,
        f"SELECT lifecycle_state FROM {_OUTBOX} WHERE id = '{episode_id}'::uuid",
    )
    assert switchboard_state == "sending"
    with pytest.raises(asyncpg.CheckViolationError, match="fenced claim identity"):
        await _execute_as_role(
            upgraded_pool,
            _SWITCHBOARD,
            f"""
            UPDATE {_OUTBOX}
            SET claim_token = gen_random_uuid(), claim_epoch = claim_epoch + 1
            WHERE id = '{episode_id}'::uuid
            """,
        )


@pytest.mark.asyncio(loop_scope="module")
async def test_switchboard_cannot_skip_claim_fencing_or_regress_delivery_lease_epoch(
    upgraded_pool: asyncpg.Pool,
) -> None:
    """REQ-runtime-attention-outbox-001 fences state and singleton-lease changes."""
    entry_id, trigger_id = await _seed_breaker_edge(upgraded_pool, "runtime-attention-fences")
    episode_id = await _call_as_role(
        upgraded_pool,
        _MODEL_PRODUCER,
        "SELECT public.append_runtime_attention_model_breaker($1)",
        trigger_id,
    )
    assert isinstance(episode_id, uuid.UUID)
    assert isinstance(entry_id, uuid.UUID)

    # A terminal state cannot be written straight from pending: it must retain
    # a fresh fenced sending claim first.
    with pytest.raises(asyncpg.CheckViolationError, match="requires a fenced sending claim"):
        await _execute_as_role(
            upgraded_pool,
            _SWITCHBOARD,
            f"""
            UPDATE {_OUTBOX}
            SET lifecycle_state = 'sent', delivered_at = now()
            WHERE id = '{episode_id}'::uuid
            """,
        )

    # A new singleton lease starts at epoch one; a stale holder cannot choose
    # an arbitrary epoch or move a live fence backwards.
    with pytest.raises(asyncpg.CheckViolationError, match="lease acquisition must advance"):
        await _execute_as_role(
            upgraded_pool,
            _SWITCHBOARD,
            """
            INSERT INTO public.runtime_attention_delivery_lease (
                lease_token, lease_epoch, holder_instance, acquired_at, expires_at
            )
            VALUES (gen_random_uuid(), 9, 'switchboard-test', now(), now() + interval '30 seconds')
            """,
        )

    await _execute_as_role(
        upgraded_pool,
        _SWITCHBOARD,
        """
        INSERT INTO public.runtime_attention_delivery_lease (
            lease_token, lease_epoch, holder_instance, acquired_at, expires_at
        )
        VALUES (gen_random_uuid(), 1, 'switchboard-test', now(), now() + interval '30 seconds')
        """,
    )
    with pytest.raises(asyncpg.CheckViolationError, match="lease epoch may not move backwards"):
        await _execute_as_role(
            upgraded_pool,
            _SWITCHBOARD,
            """
            UPDATE public.runtime_attention_delivery_lease
            SET lease_epoch = 0
            WHERE lease_name = 'runtime_attention_delivery'
            """,
        )


@pytest.mark.asyncio(loop_scope="module")
async def test_concurrent_half_open_failures_emit_one_deterministic_edge(
    upgraded_pool: asyncpg.Pool,
    upgraded_admin_pool: asyncpg.Pool,
) -> None:
    """A stale breaker reopens once even when same-timestamp failures race.

    The lower id is the first ordered new failure after the stale window, so
    it is the only valid closed-to-open transition.  Concurrent calls for the
    later equal-timestamp failure must not create a duplicate episode.
    """
    entry_id = uuid.uuid4()
    stale_ts = datetime.now(UTC) - timedelta(minutes=16)
    tied_ts = datetime.now(UTC)
    async with upgraded_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.model_catalog (id, alias, runtime_type, model_id)
            VALUES ($1, 'runtime-attention-half-open', 'codex', 'half-open-model')
            """,
            entry_id,
        )
        for _ in range(5):
            await conn.execute(
                """
                INSERT INTO public.model_dispatch_attempts (catalog_entry_id, ts, butler, outcome)
                VALUES ($1, $2, 'general', 'runtime_failure')
                """,
                entry_id,
                stale_ts,
            )
        trigger_ids = [
            await conn.fetchval(
                """
                INSERT INTO public.model_dispatch_attempts (catalog_entry_id, ts, butler, outcome)
                VALUES ($1, $2, 'general', 'runtime_failure')
                RETURNING id
                """,
                entry_id,
                tied_ts,
            )
            for _ in range(2)
        ]
    assert all(isinstance(trigger_id, int) for trigger_id in trigger_ids)

    results = await asyncio.gather(
        *[
            _call_as_role(
                upgraded_pool,
                _MODEL_PRODUCER,
                "SELECT public.append_runtime_attention_model_breaker($1)",
                trigger_id,
            )
            for trigger_id in trigger_ids
        ],
        return_exceptions=True,
    )
    episode_ids = [result for result in results if isinstance(result, uuid.UUID)]
    assert len(episode_ids) == 1
    assert any(
        isinstance(result, asyncpg.CheckViolationError) and "already open" in str(result)
        for result in results
    )
    row = await upgraded_admin_pool.fetchrow(
        f"SELECT triggering_attempt_id FROM {_OUTBOX} WHERE id = $1", episode_ids[0]
    )
    assert row is not None
    assert row["triggering_attempt_id"] == min(trigger_ids)


def test_core_chain_is_idempotent_for_two_target_schemas_without_switchboard_dependency(
    postgres_container,
) -> None:
    """The database-global outbox installs once across two core-chain targets."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core", schema="general"))
    asyncio.run(run_migrations(db_url, chain="core", schema="switchboard"))

    engine = create_engine(migration_bootstrap_db_url(postgres_container, db_name))
    try:
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM pg_class WHERE oid = 'public.runtime_attention_outbox'::regclass"
                    )
                ).scalar_one()
                == 1
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM pg_constraint "
                        "WHERE conrelid = 'public.runtime_attention_outbox'::regclass AND contype = 'f'"
                    )
                ).scalar_one()
                == 0
            )
            assert (
                conn.execute(text("SELECT version_num FROM general.alembic_version")).scalar_one()
                == "core_198"
            )
            assert (
                conn.execute(
                    text("SELECT version_num FROM switchboard.alembic_version")
                ).scalar_one()
                == "core_198"
            )
    finally:
        engine.dispose()


def test_core_chain_serializes_global_runtime_attention_install_across_processes(
    postgres_container,
) -> None:
    """Two independent schema upgrades finalize one protected interface."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    target_schemas = ("general", "switchboard")
    for target_schema in target_schemas:
        command.upgrade(
            _build_alembic_config(db_url, chains=["core"], target_schema=target_schema),
            "core_197",
        )

    process_results = _run_concurrent_core_head_upgrades(db_url, target_schemas)
    failed_processes = {
        target_schema: stderr
        for target_schema, (returncode, _stdout, stderr) in process_results.items()
        if returncode != 0
    }
    assert not failed_processes, "\n".join(failed_processes.values())

    engine = create_engine(migration_bootstrap_db_url(postgres_container, db_name))
    try:
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM pg_class "
                        "WHERE oid = 'public.runtime_attention_outbox'::regclass"
                    )
                ).scalar_one()
                == 1
            )
            assert conn.execute(
                text(
                    "SELECT relrowsecurity AND relforcerowsecurity "
                    "FROM pg_class WHERE oid = 'public.runtime_attention_outbox'::regclass"
                )
            ).scalar_one()
            for target_schema in target_schemas:
                assert (
                    conn.execute(
                        text(
                            f"SELECT version_num FROM {_quote_ident(target_schema)}.alembic_version"
                        )
                    ).scalar_one()
                    == "core_198"
                )
    finally:
        engine.dispose()

    assert _has_exact_finalized_runtime_attention_interface(db_url)


def test_core_chain_serializes_global_runtime_attention_downgrade_and_reapply_across_processes(
    postgres_container,
) -> None:
    """One target tears down the global boundary; the other records the same downgrade."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    target_schemas = ("general", "switchboard")
    for target_schema in target_schemas:
        command.upgrade(
            _build_alembic_config(db_url, chains=["core"], target_schema=target_schema),
            "core_197",
        )

    upgrade_results = _run_concurrent_core_head_upgrades(db_url, target_schemas)
    failed_upgrades = {
        target_schema: stderr
        for target_schema, (returncode, _stdout, stderr) in upgrade_results.items()
        if returncode != 0
    }
    assert not failed_upgrades, "\n".join(failed_upgrades.values())

    downgrade_results = _run_concurrent_core_197_downgrades(bootstrap_url, target_schemas)
    failed_downgrades = {
        target_schema: stderr
        for target_schema, (returncode, _stdout, stderr) in downgrade_results.items()
        if returncode != 0
    }
    assert not failed_downgrades, "\n".join(failed_downgrades.values())

    engine = create_engine(bootstrap_url)
    try:
        with engine.connect() as conn:
            for target_schema in target_schemas:
                assert (
                    conn.execute(
                        text(
                            f"SELECT version_num FROM {_quote_ident(target_schema)}.alembic_version"
                        )
                    ).scalar_one()
                    == "core_197"
                )
            for relation in (
                "public.runtime_attention_outbox",
                "public.runtime_attention_delivery_lease",
                "public.idx_model_dispatch_attempts_catalog_ts_id",
                "public.idx_model_dispatch_attempts_outcome_ts_id",
            ):
                assert conn.execute(text(f"SELECT to_regclass('{relation}') IS NULL")).scalar_one()
    finally:
        engine.dispose()
    assert not _has_bootstrap_finalized_runtime_attention_interface(bootstrap_url)

    reapply_results = _run_concurrent_core_head_upgrades(db_url, target_schemas)
    failed_reapply = {
        target_schema: stderr
        for target_schema, (returncode, _stdout, stderr) in reapply_results.items()
        if returncode != 0
    }
    assert not failed_reapply, "\n".join(failed_reapply.values())
    assert _has_exact_finalized_runtime_attention_interface(db_url)


@pytest.mark.parametrize(
    ("index_name", "index_definition"),
    [
        (
            "idx_model_dispatch_attempts_catalog_ts_id",
            "catalog_entry_id, ts DESC, id DESC",
        ),
        (
            "idx_model_dispatch_attempts_outcome_ts_id",
            "outcome, ts DESC, id DESC",
        ),
    ],
)
def test_core_chain_rejects_preexisting_exact_index_before_install_and_on_reapply(
    postgres_container,
    index_name: str,
    index_definition: str,
) -> None:
    """A reserved exact index remains external across rejected install attempts."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    _upgrade_to_core_197(db_url)

    engine = create_engine(bootstrap_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE INDEX {index_name}
                    ON public.model_dispatch_attempts ({index_definition})
                    """
                )
            )

        with pytest.raises(DBAPIError, match="reserved deterministic index already exists"):
            _upgrade_to_core_head(db_url)

        with engine.connect() as conn:
            assert conn.execute(
                text(f"SELECT to_regclass('public.{index_name}') IS NOT NULL")
            ).scalar_one()
            assert conn.execute(
                text("SELECT to_regclass('public.runtime_attention_outbox') IS NULL")
            ).scalar_one()

        # A clean install owns both reserved indexes, so its bounded empty
        # rollback may remove them before returning the installer handoff.
        with engine.begin() as conn:
            conn.execute(text(f"DROP INDEX public.{index_name}"))
        _upgrade_to_core_head(db_url)
        command.downgrade(_build_alembic_config(bootstrap_url, chains=["core"]), "core_197")
        with engine.connect() as conn:
            assert conn.execute(
                text(f"SELECT to_regclass('public.{index_name}') IS NULL")
            ).scalar_one()

        # Reapplication has the same ownership rule: an independently
        # recreated reserved name is rejected and preserved before mutation.
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE INDEX {index_name}
                    ON public.model_dispatch_attempts ({index_definition})
                    """
                )
            )
        with pytest.raises(DBAPIError, match="reserved deterministic index already exists"):
            _upgrade_to_core_head(db_url)
        with engine.connect() as conn:
            assert conn.execute(
                text(f"SELECT to_regclass('public.{index_name}') IS NOT NULL")
            ).scalar_one()
            assert conn.execute(
                text("SELECT to_regclass('public.runtime_attention_outbox') IS NULL")
            ).scalar_one()
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("index_name", "index_definition"),
    [
        (
            "idx_model_dispatch_attempts_catalog_ts_id",
            "catalog_entry_id, ts DESC, id DESC",
        ),
        (
            "idx_model_dispatch_attempts_outcome_ts_id",
            "outcome, ts DESC, id DESC",
        ),
    ],
)
def test_core_chain_rejects_partial_runtime_attention_index_across_processes(
    postgres_container,
    index_name: str,
    index_definition: str,
) -> None:
    """A same-name partial index cannot satisfy the required full index proof."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    target_schemas = ("general", "switchboard")
    for target_schema in target_schemas:
        command.upgrade(
            _build_alembic_config(db_url, chains=["core"], target_schema=target_schema),
            "core_197",
        )

    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    poison_engine = create_engine(bootstrap_url)
    try:
        with poison_engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE INDEX {index_name}
                    ON public.model_dispatch_attempts ({index_definition})
                    WHERE false
                    """
                )
            )
    finally:
        poison_engine.dispose()

    process_results = _run_concurrent_core_head_upgrades(db_url, target_schemas)
    successful_processes = {
        target_schema: stdout
        for target_schema, (returncode, stdout, _stderr) in process_results.items()
        if returncode == 0
    }
    assert not successful_processes

    failed_processes = {
        target_schema: stderr
        for target_schema, (returncode, _stdout, stderr) in process_results.items()
        if returncode != 0
    }
    assert all(
        "runtime-attention reserved deterministic index already exists" in stderr
        for stderr in failed_processes.values()
    )

    engine = create_engine(bootstrap_url)
    try:
        with engine.connect() as conn:
            for target_schema in target_schemas:
                assert (
                    conn.execute(
                        text(
                            f"SELECT version_num FROM {_quote_ident(target_schema)}.alembic_version"
                        )
                    ).scalar_one()
                    == "core_197"
                )
            assert (
                conn.execute(
                    text("SELECT to_regclass('public.runtime_attention_outbox')")
                ).scalar_one()
                is None
            )
            assert (
                conn.execute(
                    text("SELECT to_regclass('public.runtime_attention_delivery_lease')")
                ).scalar_one()
                is None
            )
    finally:
        engine.dispose()

    assert not _has_exact_finalized_runtime_attention_interface(db_url)


@pytest.mark.parametrize(
    ("index_name", "index_definition", "predicate_type", "predicate_column"),
    [
        (
            "idx_model_dispatch_attempts_catalog_ts_id",
            "catalog_entry_id, ts DESC, id DESC",
            "uuid",
            "catalog_entry_id",
        ),
        (
            "idx_model_dispatch_attempts_outcome_ts_id",
            "outcome, ts DESC, id DESC",
            "text",
            "outcome",
        ),
    ],
)
def test_core_chain_rejects_invalid_runtime_attention_index_across_processes(
    postgres_container,
    index_name: str,
    index_definition: str,
    predicate_type: str,
    predicate_column: str,
) -> None:
    """An invalid same-name index cannot make the core_198 catalog proof pass."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    target_schemas = ("general", "switchboard")
    for target_schema in target_schemas:
        command.upgrade(
            _build_alembic_config(db_url, chains=["core"], target_schema=target_schema),
            "core_197",
        )

    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    seed_engine = create_engine(bootstrap_url)
    try:
        with seed_engine.begin() as conn:
            entry_id = uuid.uuid4()
            conn.execute(
                text(
                    """
                    INSERT INTO public.model_catalog (id, alias, runtime_type, model_id)
                    VALUES (:id, 'core-198-invalid-index', 'codex', 'invalid-index-model')
                    """
                ),
                {"id": entry_id},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO public.model_dispatch_attempts (
                        catalog_entry_id, butler, outcome
                    )
                    VALUES (:catalog_entry_id, 'general', 'runtime_failure')
                    """
                ),
                {"catalog_entry_id": entry_id},
            )
    finally:
        seed_engine.dispose()

    poison_engine = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    try:
        with poison_engine.connect() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE FUNCTION public.core_198_poison_index_predicate(value {predicate_type})
                    RETURNS boolean
                    LANGUAGE plpgsql
                    IMMUTABLE
                    AS $$
                    BEGIN
                        RAISE EXCEPTION 'core_198 invalid-index fixture';
                    END;
                    $$
                    """
                )
            )
            with pytest.raises(DBAPIError, match="core_198 invalid-index fixture"):
                conn.execute(
                    text(
                        f"""
                        CREATE INDEX CONCURRENTLY {index_name}
                        ON public.model_dispatch_attempts ({index_definition})
                        WHERE public.core_198_poison_index_predicate({predicate_column})
                        """
                    )
                )
            assert conn.execute(
                text(
                    f"""
                    SELECT NOT index_row.indisvalid AND NOT index_row.indisready
                    FROM pg_index AS index_row
                    WHERE index_row.indexrelid =
                        'public.{index_name}'::regclass
                    """
                )
            ).scalar_one()
    finally:
        poison_engine.dispose()

    process_results = _run_concurrent_core_head_upgrades(db_url, target_schemas)
    successful_processes = {
        target_schema: stdout
        for target_schema, (returncode, stdout, _stderr) in process_results.items()
        if returncode == 0
    }
    assert not successful_processes
    failed_processes = {
        target_schema: stderr
        for target_schema, (returncode, _stdout, stderr) in process_results.items()
        if returncode != 0
    }
    assert all(
        "runtime-attention reserved deterministic index already exists" in stderr
        for stderr in failed_processes.values()
    )
    assert not _has_exact_finalized_runtime_attention_interface(db_url)


@pytest.mark.parametrize(
    ("index_name", "index_definition"),
    [
        (
            "idx_model_dispatch_attempts_catalog_ts_id",
            "catalog_entry_id, ts ASC, id DESC",
        ),
        (
            "idx_model_dispatch_attempts_outcome_ts_id",
            "outcome, ts ASC, id DESC",
        ),
    ],
)
def test_core_chain_rejects_wrong_direction_runtime_attention_index_across_processes(
    postgres_container,
    index_name: str,
    index_definition: str,
) -> None:
    """The exact catalog proof rejects either index with a wrong sort direction."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    target_schemas = ("general", "switchboard")
    for target_schema in target_schemas:
        command.upgrade(
            _build_alembic_config(db_url, chains=["core"], target_schema=target_schema),
            "core_197",
        )

    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    poison_engine = create_engine(bootstrap_url)
    try:
        with poison_engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE INDEX {index_name}
                    ON public.model_dispatch_attempts ({index_definition})
                    """
                )
            )
    finally:
        poison_engine.dispose()

    process_results = _run_concurrent_core_head_upgrades(db_url, target_schemas)
    successful_processes = {
        target_schema: stdout
        for target_schema, (returncode, stdout, _stderr) in process_results.items()
        if returncode == 0
    }
    assert not successful_processes
    failed_processes = {
        target_schema: stderr
        for target_schema, (returncode, _stdout, stderr) in process_results.items()
        if returncode != 0
    }
    assert all(
        "runtime-attention reserved deterministic index already exists" in stderr
        for stderr in failed_processes.values()
    )
    assert not _has_exact_finalized_runtime_attention_interface(db_url)


def test_actual_init_db_rerun_repairs_function_only_acl_and_effective_roles(
    postgres_container,
) -> None:
    """Rerunning bootstrap cannot restore broad producer/connector public DML."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    _upgrade_to_core_head(db_url)
    _rerun_actual_init_db(bootstrap_url, db_url)
    _entry_id, trigger_id = _seed_legacy_breaker_edge(db_url)

    def execute_as(role: str | None, statement: str):
        engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                if role is not None:
                    conn.execute(text(f"SET ROLE {_quote_ident(role)}"))
                try:
                    return conn.execute(
                        text(statement), {"trigger_id": trigger_id}
                    ).scalar_one_or_none()
                finally:
                    if role is not None:
                        conn.execute(text("RESET ROLE"))
        finally:
            engine.dispose()

    # The shared login inherits ordinary runtime memberships, but no active
    # role is not a producer identity.  ACL inheritance alone must not pass.
    with pytest.raises(DBAPIError, match="active canonical SET ROLE"):
        execute_as(None, "SELECT public.append_runtime_attention_model_breaker(:trigger_id)")

    episode_id = execute_as(
        _MODEL_PRODUCER, "SELECT public.append_runtime_attention_model_breaker(:trigger_id)"
    )
    assert isinstance(episode_id, uuid.UUID)

    for role, statement in (
        (
            _MODEL_PRODUCER,
            f"INSERT INTO {_OUTBOX} (source, source_snapshot, payload) "
            "VALUES ('model_breaker', '{}'::jsonb, '{}'::jsonb)",
        ),
        (_CONNECTOR, "SELECT public.append_runtime_attention_model_breaker(:trigger_id)"),
    ):
        with pytest.raises(DBAPIError):
            execute_as(role, statement)

    # A role with only PUBLIC grants and a deliberately nonproducer role both
    # lack the narrowly granted function interface.
    migration_role = unquote(urlparse(db_url).username or "")
    nonproducer_role = f"runtime_attention_nonproducer_{db_name}"
    public_only_role = f"runtime_attention_public_only_{db_name}"
    admin = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f"CREATE ROLE {_quote_ident(nonproducer_role)} NOLOGIN"))
            conn.execute(text(f"CREATE ROLE {_quote_ident(public_only_role)} NOLOGIN"))
            conn.execute(
                text(
                    f"GRANT {_quote_ident(nonproducer_role)} TO {_quote_ident(migration_role)} WITH INHERIT TRUE"
                )
            )
            conn.execute(
                text(
                    f"GRANT {_quote_ident(nonproducer_role)} TO {_quote_ident(migration_role)} WITH SET TRUE"
                )
            )
            # Deliberately simulate an accidental direct EXECUTE grant.  The
            # function must still reject an active role outside the canonical
            # producer allowlist; ACL membership alone is not a producer
            # identity in this shared-login topology.
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {_quote_ident(nonproducer_role)}"))
            conn.execute(
                text(
                    "GRANT EXECUTE ON FUNCTION "
                    "public.append_runtime_attention_model_breaker(bigint) "
                    f"TO {_quote_ident(nonproducer_role)}"
                )
            )
            conn.execute(text(f"SET ROLE {_quote_ident(public_only_role)}"))
            try:
                with pytest.raises(DBAPIError):
                    conn.execute(
                        text("SELECT public.append_runtime_attention_model_breaker(:trigger_id)"),
                        {"trigger_id": trigger_id},
                    )
            finally:
                conn.execute(text("RESET ROLE"))
    finally:
        admin.dispose()
    with pytest.raises(DBAPIError, match="active canonical SET ROLE"):
        execute_as(
            nonproducer_role, "SELECT public.append_runtime_attention_model_breaker(:trigger_id)"
        )

    # Switchboard alone gets direct read/claim access, and its RLS policy also
    # requires the active role (the un-set shared-login read fails below).
    assert execute_as(_SWITCHBOARD, f"SELECT count(*) FROM {_OUTBOX}") == 1
    with pytest.raises(DBAPIError, match="SET ROLE butler_switchboard_rw"):
        execute_as(None, f"SELECT count(*) FROM {_OUTBOX}")


def test_empty_outbox_can_downgrade_and_reupgrade_through_bootstrap(
    postgres_container,
) -> None:
    """A bootstrap round-trip preserves the later ordinary-role no-op proof."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    _upgrade_to_core_head(db_url)
    config = _build_alembic_config(bootstrap_url, chains=["core"])
    command.downgrade(config, "core_197")
    engine = create_engine(bootstrap_url)
    try:
        with engine.connect() as conn:
            assert conn.execute(text(f"SELECT to_regclass('{_OUTBOX}') IS NULL")).scalar_one()
            assert conn.execute(
                text(
                    "SELECT to_regclass('public.idx_model_dispatch_attempts_catalog_ts_id') IS NULL"
                )
            ).scalar_one()
            assert conn.execute(
                text(
                    "SELECT to_regclass('public.idx_model_dispatch_attempts_outcome_ts_id') IS NULL"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    command.upgrade(config, "core@head")
    assert _has_bootstrap_finalized_runtime_attention_interface(bootstrap_url)
    # The regular migration role must still recognize the exact finalized
    # interface without becoming a bootstrap fallback.
    _upgrade_to_core_head(db_url)
    assert _has_exact_finalized_runtime_attention_interface(db_url)
    engine = create_engine(bootstrap_url)
    try:
        with engine.connect() as conn:
            assert conn.execute(text(f"SELECT to_regclass('{_OUTBOX}') IS NOT NULL")).scalar_one()
            assert conn.execute(
                text(
                    "SELECT to_regclass('public.idx_model_dispatch_attempts_catalog_ts_id') IS NOT NULL"
                )
            ).scalar_one()
            assert conn.execute(
                text(
                    "SELECT to_regclass('public.idx_model_dispatch_attempts_outcome_ts_id') IS NOT NULL"
                )
            ).scalar_one()
    finally:
        engine.dispose()


def test_nonempty_outbox_rejects_downgrade_and_requires_forward_remediation(
    postgres_container,
) -> None:
    """REQ-runtime-attention-outbox-001: rollback never deletes durable evidence."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    _upgrade_to_core_head(db_url)
    engine = create_engine(bootstrap_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                        INSERT INTO {_OUTBOX} (source, fleet_halt_month, lifecycle_state, source_snapshot, payload)
                    VALUES (
                        'fleet_halt',
                        date '2026-08-01',
                        'pending',
                        jsonb_build_object(
                            'month', '2026-08', 'denied_count', 1, 'first_denied_at', NULL
                        ),
                        jsonb_build_object(
                            'classification', 'monthly_spend_ceiling',
                            'door', '/spend?outcome=quota_skip'
                        )
                    )
                    """
                )
            )
    finally:
        engine.dispose()

    config = _build_alembic_config(bootstrap_url, chains=["core"])
    with pytest.raises(DBAPIError, match="forward remediation"):
        command.downgrade(config, "core_197")

    _upgrade_to_core_head(db_url)
    engine = create_engine(bootstrap_url)
    try:
        with engine.connect() as conn:
            assert conn.execute(text(f"SELECT count(*) FROM {_OUTBOX}")).scalar_one() == 1
    finally:
        engine.dispose()
