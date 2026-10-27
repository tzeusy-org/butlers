"""Real-Postgres contract tests for the inert runtime-attention outbox.

REQ-runtime-attention-outbox-001 defines durable, safe episode representation
without migration-time paging or historical backfill.  REQ-database-security-007
requires effective-role (rather than source-only) proof that producers can only
perform validated appends while Switchboard owns delivery transitions.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import select
import shutil
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.core.audit import append as append_audit
from butlers.db_authority.apply import transition_runtime_attention
from butlers.db_authority.interfaces import (
    _attention_stage,
    function_matches,
    inspect_function,
    install_revision_group,
    selected_protected_functions,
)
from butlers.db_authority.roles import AuthorityError, compile_deployment_profiles
from butlers.migrations import (
    _build_alembic_config,
    finalize_authority,
    get_chain_head,
    migration_session,
    run_migrations,
)
from butlers.testing.migration import (
    _bootstrap_migration_prerequisites,
    _upgrade_chain_to_revision,
    assert_at_chain_head,
    migration_bootstrap_db_url,
    migration_db_name,
)
from butlers.testing.migration import (
    create_migration_db as _create_migration_db,
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
_DASHBOARD = "dashboard_api"
_CONNECTOR = "connector_writer"
_FUNCTION_MODEL_BREAKER = "public.append_runtime_attention_model_breaker(bigint)"
_FUNCTION_FLEET_HALT = "public.append_runtime_attention_fleet_halt()"
# These mappings identify only test-owned administrative databases. Serving
# Database identity/admission is tested by its separate native owner.
_MAINTENANCE_URLS: dict[str, str] = {}
_NORMAL_URLS: dict[str, str] = {}
_SELECTED_PLANS: dict[str, dict[str | None, str | None]] = {}
_ROLE_CONTROL_URLS: dict[asyncpg.Pool, str] = {}
_NORMAL_POOL_URLS: dict[asyncpg.Pool, str] = {}


def create_migration_db(postgres_container, db_name: str) -> str:
    db_url = _create_migration_db(postgres_container, db_name)
    _MAINTENANCE_URLS[db_name] = migration_bootstrap_db_url(postgres_container, db_name)
    _NORMAL_URLS[db_name] = db_url
    _SELECTED_PLANS[db_name] = {}
    return db_url


def _database(db_url: str) -> str:
    return unquote(urlparse(db_url).path.lstrip("/"))


def _maintenance_url(db_url: str) -> str:
    return _MAINTENANCE_URLS[_database(db_url)]


def _selected_upgrade(config, target: str) -> None:
    db_url = _NORMAL_URLS[_database(config.get_main_option("sqlalchemy.url"))]
    schema = config.get_main_option("butlers.target_schema") or None
    revision = None if target == "core@head" else target
    with migration_session(_maintenance_url(db_url), source_database="butlers") as held:
        if revision is None:
            asyncio.run(run_migrations(db_url, chain="core", schema=schema, connection=held))
        else:
            _upgrade_chain_to_revision(
                db_url, chain="core", schema=schema, revision=revision, connection=held
            )
        plans = dict(_SELECTED_PLANS[_database(db_url)])
        plans[schema] = revision
        # Historical stamps stage real source before seeds; current admission follows head.
        if revision is None:
            finalize_authority(
                held,
                source_database="butlers",
                plans=tuple(("core", selected, value) for selected, value in plans.items()),
            )
    _SELECTED_PLANS[_database(db_url)] = plans


def _selected_downgrade(config, target: str) -> None:
    # This is an actual unprepared reverse request. Current source must refuse
    # before effects; only the separate source-owned R0/R1 operator can reverse.
    with migration_session(
        _maintenance_url(config.get_main_option("sqlalchemy.url")), source_database="butlers"
    ) as held:
        config.attributes["connection"] = held
        command.downgrade(config, target)


def _empty_install_rollback(db_url: str) -> None:
    # A transaction rollback proves current installer atomicity without reviving
    # the retired stored teardown or claiming an operator cutover occurred.
    with migration_session(_maintenance_url(db_url), source_database="butlers") as held:
        install_revision_group(held, "attention")
        assert held.execute(
            text("SELECT to_regclass('public.runtime_attention_outbox') IS NOT NULL")
        ).scalar_one()
        held.rollback()


def _attention_transition(db_url: str, component: str, enabled: bool, *, maintenance: bool = True):
    """Run the real preserving operator on its actual held physical session.

    Ordinary identity is selected only for explicit refusal controls. No test
    control-row UPDATE, stored upgrade function or prepared-reverse flag exists.
    """
    url = _maintenance_url(db_url) if maintenance else _NORMAL_URLS[_database(db_url)]
    with migration_session(url, source_database="butlers") as held:
        result = transition_runtime_attention(held, component=component, enabled=enabled)
        held.commit()
        return result


def _attention_snapshot(db_url: str):
    """Independent immutable catalog, protected data and actual-stamp observer."""
    engine = create_engine(_maintenance_url(db_url), connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as conn:
            catalog = conn.execute(
                text("""
                SELECT 'relation', c.oid::text, n.nspname, c.relname,
                       pg_get_userbyid(c.relowner), c.relrowsecurity::text,
                       c.relforcerowsecurity::text, c.relacl::text
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relname LIKE 'runtime_attention%'
                UNION ALL
                SELECT 'function', p.oid::text, n.nspname, p.proname,
                       pg_get_userbyid(p.proowner), p.prosecdef::text,
                       p.proconfig::text, pg_get_functiondef(p.oid) || COALESCE(p.proacl::text,'')
                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='public' AND (p.proname LIKE '%runtime_attention%'
                    OR p.proname='hashtextextended')
                ORDER BY 1,2
            """)
            ).all()
            policies = conn.execute(
                text("""
                SELECT p.polrelid, p.polname, p.polcmd, p.polpermissive, p.polroles,
                       pg_get_expr(p.polqual,p.polrelid),pg_get_expr(p.polwithcheck,p.polrelid)
                FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
                WHERE c.relname LIKE 'runtime_attention%' ORDER BY 1,2
            """)
            ).all()
            triggers = conn.execute(
                text("""
                SELECT t.oid,pg_get_triggerdef(t.oid) FROM pg_trigger t
                JOIN pg_class c ON c.oid=t.tgrelid
                WHERE c.relname LIKE 'runtime_attention%' AND NOT t.tgisinternal ORDER BY 1
            """)
            ).all()
            constraints = conn.execute(
                text("""
                SELECT c.oid, c.conrelid, c.conname, c.convalidated, pg_get_constraintdef(c.oid)
                FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid
                WHERE r.relname LIKE 'runtime_attention%' ORDER BY 1
            """)
            ).all()
            indexes = conn.execute(
                text("""
                SELECT i.indexrelid,i.indrelid,i.indisvalid,i.indisready,pg_get_indexdef(i.indexrelid)
                FROM pg_index i JOIN pg_class r ON r.oid=i.indrelid
                WHERE r.relname LIKE 'runtime_attention%' OR r.relname='model_dispatch_attempts'
                ORDER BY 1
            """)
            ).all()
            namespaces = conn.execute(
                text("""
                SELECT oid,nspname,nspowner,nspacl FROM pg_namespace
                WHERE nspname IN ('public','runtime_attention_admin') ORDER BY 1
            """)
            ).all()
            data = {}
            for name in (
                "runtime_attention_outbox",
                "runtime_attention_delivery_lease",
                "runtime_attention_producer_control",
                "runtime_attention_operator_control",
                "runtime_attention_condition_control",
                "runtime_attention_condition_episodes",
                "infra_conditions",
            ):
                data[name] = conn.execute(
                    text(
                        f"SELECT to_jsonb(data_row)::text FROM public.{_quote_ident(name)} AS data_row "
                        "ORDER BY to_jsonb(data_row)::text"
                    )
                ).all()
            stamps = {}
            for schema in _SELECTED_PLANS[_database(db_url)]:
                namespace = schema or "public"
                stamps[namespace] = conn.execute(
                    text(
                        f"SELECT version_num FROM {_quote_ident(namespace)}.alembic_version ORDER BY 1"
                    )
                ).all()
            return {
                "catalog": catalog,
                "policies": policies,
                "triggers": triggers,
                "constraints": constraints,
                "indexes": indexes,
                "namespaces": namespaces,
                "data": data,
                "stamps": stamps,
            }
    finally:
        engine.dispose()


def _append_synthetic_legacy_history(db_url: str, entry_id: uuid.UUID):
    """Real audit.append receipts; legacy text is data, never writer authority."""

    async def write():
        connection = await asyncpg.connect(
            _asyncpg_url(_maintenance_url(db_url)), timeout=10, command_timeout=30
        )
        try:
            await _decode_jsonb(connection)
            await connection.execute(f"SET ROLE {_quote_ident(_MODEL_PRODUCER)}")
            ids = []
            for action, target, note in (
                ("model_breaker_open_notified", f"model_breaker:{entry_id}", "blocked_old_binary"),
                ("ceiling_halt_notified", "ceiling_halt", "synthetic retained legacy month"),
            ):
                receipt = await append_audit(
                    connection, _LEGACY_MARKER_ACTOR, action, target=target, note=note
                )
                assert type(receipt) is int and receipt > 0
                ids.append(receipt)
            return ids
        finally:
            try:
                await asyncio.wait_for(connection.close(), timeout=10)
            except BaseException:
                connection.terminate()
                raise

    return asyncio.run(write())


def _validated_current_episode(db_url: str, *, fleet: bool = False):
    async def produce():
        pool = await asyncpg.create_pool(
            _asyncpg_url(_maintenance_url(db_url)), min_size=1, max_size=2, init=_decode_jsonb
        )
        try:
            if fleet:
                await _seed_fleet_halt_evidence(pool)
                return await _call_as_role(
                    pool, _MODEL_PRODUCER, "SELECT public.append_runtime_attention_fleet_halt()"
                )
            _entry_id, attempt = await _seed_breaker_edge(pool, f"current-{uuid.uuid4()}")
            return await _call_as_role(
                pool,
                _MODEL_PRODUCER,
                "SELECT public.append_runtime_attention_model_breaker($1)",
                attempt,
            )
        finally:
            await _close_pool(pool)

    return asyncio.run(produce())


# Durable evidence a rollback must refuse to delete, written straight to the
# outbox under bootstrap authority rather than through a producer.
_INSERT_FLEET_HALT_EVIDENCE_SQL = f"""
    INSERT INTO {_OUTBOX} (
        source, fleet_halt_month, lifecycle_state, source_snapshot, payload
    )
    VALUES (
        'fleet_halt',
        date '2026-08-01',
        'pending',
        jsonb_build_object('month', '2026-08', 'denied_count', 1, 'first_denied_at', NULL),
        jsonb_build_object(
            'classification', 'monthly_spend_ceiling',
            'door', '/spend?outcome=quota_skip'
        )
    )
"""


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _upgrade_to_core_197(db_url: str) -> None:
    _selected_upgrade(_build_alembic_config(db_url, chains=["core"]), "core_197")


def _upgrade_to_core_head(db_url: str) -> None:
    _selected_upgrade(_build_alembic_config(db_url, chains=["core"]), "core@head")


def _source_attention_definitions(profiles, operator, plans):
    """Execute the actual source compiler rather than load a retired installer."""
    definitions = tuple(
        row
        for row in selected_protected_functions(profiles, operator, plans)
        if row.owner == "runtime_attention_outbox_owner"
    )
    assert profiles, "actual source deployment must contain runtime profiles"
    assert len({row.identity for row in definitions}) == len(definitions), (
        "source-selected attention definitions must have unambiguous identities"
    )
    return definitions


def _has_finalized_runtime_attention_interface(db_url: str, proof_sql: str) -> bool:
    """Read back actual current/historical source-defined attention catalogs.

    The former proof_sql label selects no SQL/authority. Bodies, ACLs, protected
    shapes and retired namespace absence come from the actual source compiler.
    """
    del proof_sql
    database = _database(db_url)
    plans = tuple(
        ("core", schema, revision) for schema, revision in _SELECTED_PLANS[database].items()
    ) or (("core", None, None),)
    with migration_session(_maintenance_url(db_url), source_database="butlers") as conn:
        normal = unquote(urlparse(_NORMAL_URLS[database]).username or "")
        profiles = compile_deployment_profiles(database, source_database="butlers")
        definitions = _source_attention_definitions(profiles, normal, plans)
        if not definitions:
            return False
        if not all(function_matches(conn, row, inspect_function(conn, row)) for row in definitions):
            return False
        if _attention_stage(conn, profiles=profiles) != 5:
            return False
        return (
            conn.execute(
                text(
                    "SELECT NOT EXISTS (SELECT FROM pg_catalog.pg_namespace "
                    "WHERE nspname='runtime_attention_admin')"
                )
            ).scalar_one()
            is True
        )


def _has_exact_finalized_runtime_attention_interface(db_url: str) -> bool:
    """Current source catalog proof, shared by both administrative callers."""
    return _has_finalized_runtime_attention_interface(db_url, "source-current")


def _has_bootstrap_finalized_runtime_attention_interface(db_url: str) -> bool:
    """Current source catalog proof; no bootstrap-stored callable is adopted."""
    return _has_finalized_runtime_attention_interface(db_url, "source-current")


_CORE_HEAD_UPGRADE_PROCESS = """
import asyncio
import os
import sys
import traceback
from butlers.migrations import finalize_authority, migration_session, run_migrations

db_url, maintenance_url, target_schema, ready_fd, release_fd = sys.argv[1:]
try:
    os.write(int(ready_fd), b"1")
    if os.read(int(release_fd), 1) != b"1":
        raise RuntimeError("concurrent core-upgrade barrier was not released")
    with migration_session(maintenance_url, source_database="butlers") as held:
        asyncio.run(run_migrations(db_url, chain="core", schema=target_schema, connection=held))
        finalize_authority(held, source_database="butlers", plans=(("core",target_schema,None),))
except BaseException:
    traceback.print_exc()
    raise
"""


_CORE_197_DOWNGRADE_PROCESS = """
import os
import sys
import traceback
from alembic import command
from butlers.migrations import _build_alembic_config, migration_session

db_url, maintenance_url, target_schema, ready_fd, release_fd = sys.argv[1:]
try:
    os.write(int(ready_fd), b"1")
    if os.read(int(release_fd), 1) != b"1":
        raise RuntimeError("concurrent core-downgrade barrier was not released")
    with migration_session(maintenance_url, source_database="butlers") as held:
        config = _build_alembic_config(db_url, chains=["core"], target_schema=target_schema)
        config.attributes["connection"] = held
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
                    _maintenance_url(db_url),
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
        for schema, (status, _stdout, _stderr) in results.items():
            if status == 0:
                _SELECTED_PLANS[_database(db_url)][schema] = None
        return results
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        for process in processes.values():
            if process.poll() is None:
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
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
                    _maintenance_url(db_url),
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
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
        for file_descriptor in (ready_read, ready_write, release_read, release_write):
            if file_descriptor >= 0:
                os.close(file_descriptor)


def _rerun_actual_init_db(bootstrap_url: str, migration_url: str) -> None:
    """Execute current production preparation and held source finalization.

    No ad-hoc object grants or stored installer are supplied by this fixture.
    Administrative preparation is distinct from normal migration/serving login.
    """
    migration_role = unquote(urlparse(migration_url).username or "")
    assert migration_role
    _bootstrap_migration_prerequisites(bootstrap_url, migration_role)
    with migration_session(bootstrap_url, source_database="butlers") as held:
        plans = tuple(
            ("core", schema, revision)
            for schema, revision in _SELECTED_PLANS[_database(migration_url)].items()
        )
        finalize_authority(held, source_database="butlers", plans=plans)


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
    _upgrade_to_core_head(db_url)
    return db_url


@pytest_asyncio.fixture(loop_scope="module")
async def upgraded_pool(upgraded_db_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(upgraded_db_url, min_size=1, max_size=3, init=_decode_jsonb)
    _ROLE_CONTROL_URLS[pool] = _asyncpg_url(_maintenance_url(upgraded_db_url))
    _NORMAL_POOL_URLS[pool] = upgraded_db_url
    try:
        yield pool
    finally:
        _ROLE_CONTROL_URLS.pop(pool, None)
        _NORMAL_POOL_URLS.pop(pool, None)
        await _close_pool(pool)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def core_only_pool(core_only_db_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(core_only_db_url, min_size=1, max_size=2, init=_decode_jsonb)
    _ROLE_CONTROL_URLS[pool] = _asyncpg_url(_maintenance_url(core_only_db_url))
    try:
        yield pool
    finally:
        _ROLE_CONTROL_URLS.pop(pool, None)
        await _close_pool(pool)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def core_only_admin_pool(postgres_container, core_only_db_url: str) -> asyncpg.Pool:
    db_name = urlparse(core_only_db_url).path.lstrip("/")
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name).replace(
        "postgresql+psycopg2://", "postgresql://", 1
    )
    pool = await asyncpg.create_pool(bootstrap_url, min_size=1, max_size=2, init=_decode_jsonb)
    try:
        yield pool
    finally:
        await _close_pool(pool)


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
    try:
        yield pool
    finally:
        await _close_pool(pool)


async def _close_pool(pool: asyncpg.Pool) -> None:
    try:
        await asyncio.wait_for(pool.close(), timeout=10)
    except BaseException:
        pool.terminate()
        raise


@asynccontextmanager
async def _effective_role_connection(pool: asyncpg.Pool):
    control_url = _ROLE_CONTROL_URLS.get(pool)
    if control_url is None:
        async with pool.acquire() as connection:
            yield connection
        return
    connection = await asyncpg.connect(control_url, timeout=10, command_timeout=30)
    try:
        await _decode_jsonb(connection)
        yield connection
    finally:
        try:
            await asyncio.wait_for(connection.close(), timeout=10)
        except BaseException:
            connection.terminate()
            raise


async def _call_as_role(
    pool: asyncpg.Pool,
    role: str,
    statement: str,
    *args: object,
) -> object:
    async with _effective_role_connection(pool) as conn:
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
    async with _effective_role_connection(pool) as conn:
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
    async with _effective_role_connection(pool) as conn:
        await conn.execute(f"SET ROLE {_quote_ident(role)}")
        try:
            return await conn.fetchrow(statement, *args)
        finally:
            await conn.execute("RESET ROLE")


async def _fetch_as_role(
    pool: asyncpg.Pool,
    role: str,
    statement: str,
    *args: object,
) -> list[asyncpg.Record]:
    async with _effective_role_connection(pool) as conn:
        await conn.execute(f"SET ROLE {_quote_ident(role)}")
        try:
            return await conn.fetch(statement, *args)
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
                    'search_path=pg_catalog, pg_temp'
                ]::text[] AS fixed_definer_path,
            fleet_halt.prosecdef
                AND fleet_halt.proconfig = ARRAY[
                    'search_path=pg_catalog, pg_temp'
                ]::text[] AS fixed_fleet_path,
            lease_guard.prosecdef
                AND lease_guard.proconfig = ARRAY[
                    'search_path=pg_catalog, pg_temp'
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
                SELECT 1 FROM pg_catalog.pg_namespace
                WHERE nspname='runtime_attention_admin'
            ) AND NOT EXISTS (
                SELECT FROM pg_catalog.pg_proc p
                JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='public' AND (p.proname LIKE 'runtime_attention_upgrade_%'
                     OR p.proname LIKE 'runtime_attention_deactivate_%')
            ) AS retired_repair_unavailable
        FROM pg_roles AS owner_role
        JOIN pg_proc AS model_breaker
          ON model_breaker.oid = 'public.append_runtime_attention_model_breaker(bigint)'::regprocedure
        JOIN pg_proc AS fleet_halt
          ON fleet_halt.oid = 'public.append_runtime_attention_fleet_halt()'::regprocedure
        JOIN pg_proc AS lease_guard
          ON lease_guard.oid = 'public.runtime_attention_delivery_lease_guard()'::regprocedure
        WHERE owner_role.rolname = 'runtime_attention_outbox_owner'
        """
    )
    assert acl_shape is not None
    assert all(dict(acl_shape).values())


@pytest.mark.asyncio(loop_scope="module")
async def test_core_only_outbox_stages_bounded_delivery_evidence_with_switchboard_write_access(
    core_only_admin_pool: asyncpg.Pool,
) -> None:
    """REQ-runtime-attention-outbox-001 and REQ-database-security-007.

    Delivery data stays bounded to the closed CHECK-constraint vocabulary --
    that half of "safe and inert" is unconditional. But the delivery worker is
    now activated (roster/switchboard/modules/__init__.py schedules it at
    startup), so the earlier "unavailable for runtime-role updates" carve-out
    is lifted: Switchboard's UPDATE grant covers these columns so a proven
    terminal delivery outcome can actually reach the CHECK constraint, the API
    safe-reason projection, and the UI.
    """
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
            has_column_privilege(
                'butler_switchboard_rw'::regrole,
                'public.runtime_attention_outbox'::regclass,
                'delivery_error_class',
                'UPDATE'
            )
            AND has_column_privilege(
                'butler_switchboard_rw'::regrole,
                'public.runtime_attention_outbox'::regclass,
                'delivery_error_detail',
                'UPDATE'
            )
            AND has_column_privilege(
                'butler_switchboard_rw'::regrole,
                'public.runtime_attention_outbox'::regclass,
                'notification_ref',
                'UPDATE'
            )
        """
    )

    # The grant is real, not just catalog-visible: Switchboard can write a
    # vocabulary-valid terminal evidence pair to a row it currently claims ...
    async def _seed_claimed_row(month: str) -> uuid.UUID:
        return await core_only_admin_pool.fetchval(
            f"""
            INSERT INTO {_OUTBOX} (
                source, fleet_halt_month, source_snapshot, payload,
                lifecycle_state, claim_token, claim_epoch, delivery_lease_epoch,
                claimed_by_instance, claimed_at, claim_expires_at
            )
            VALUES (
                'fleet_halt', date '{month}',
                jsonb_build_object('month', '{month}', 'denied_count', 1, 'first_denied_at', NULL),
                jsonb_build_object('classification', 'monthly_spend_ceiling', 'door', '/spend'),
                'sending', gen_random_uuid(), 1, 1,
                'migration-contract-test', now(), now() + interval '30 seconds'
            )
            RETURNING id
            """
        )

    valid_id = await _seed_claimed_row("2099-04-01")
    invalid_id = await _seed_claimed_row("2099-04-02")
    async with core_only_admin_pool.acquire() as connection:
        await connection.execute('SET ROLE "butler_switchboard_rw"')
        try:
            written = await connection.fetchrow(
                f"""
                UPDATE {_OUTBOX}
                SET lifecycle_state = 'uncertain',
                    delivery_error_class = 'transport_uncertain',
                    delivery_error_detail = 'transport_timeout',
                    notification_ref = gen_random_uuid()
                WHERE id = $1
                RETURNING delivery_error_class, delivery_error_detail, notification_ref
                """,
                valid_id,
            )
            # ... but a class/detail pair outside the closed vocabulary is
            # still rejected by the CHECK constraint on the same sending ->
            # uncertain transition, not merely by the grant.
            with pytest.raises(asyncpg.CheckViolationError, match="delivery_evidence"):
                await connection.execute(
                    f"""
                    UPDATE {_OUTBOX}
                    SET lifecycle_state = 'uncertain',
                        delivery_error_class = 'transport_uncertain',
                        delivery_error_detail = 'provider_secret=leak'
                    WHERE id = $1
                    """,
                    invalid_id,
                )
        finally:
            await connection.execute("RESET ROLE")
    assert written is not None
    assert written["delivery_error_class"] == "transport_uncertain"
    assert written["delivery_error_detail"] == "transport_timeout"
    assert isinstance(written["notification_ref"], uuid.UUID)


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

    observed_models = {
        observed["catalog_entry_id"]: observed
        for observed in await _fetch_as_role(
            upgraded_admin_pool,
            _DASHBOARD,
            "SELECT * FROM public.observe_runtime_attention_models()",
        )
    }
    assert observed_models[entry_id]["episode_id"] == episode_id
    observed_fleet = await _fetchrow_as_role(
        upgraded_admin_pool,
        _DASHBOARD,
        "SELECT * FROM public.observe_runtime_attention_fleet_halt()",
    )
    assert observed_fleet is not None
    assert observed_fleet["episode_id"] == fleet_episode_id
    for observer in ("observe_runtime_attention_models", "observe_runtime_attention_fleet_halt"):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await upgraded_pool.fetch(f"SELECT * FROM public.{observer}()")


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
        f"SELECT triggering_attempt_id, source_snapshot, payload FROM {_OUTBOX} WHERE id = $1",
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

    reissue_snapshot = {
        **retained["source_snapshot"],
        "reissue_of": str(retained_episode_id),
    }
    reissue_payload = retained["payload"]
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
async def test_operator_rollback_disables_reissue_without_deleting_evidence_or_attempts(
    upgraded_pool: asyncpg.Pool,
    upgraded_admin_pool: asyncpg.Pool,
) -> None:
    """REQ-runtime-attention-outbox-003: rollback is additive and evidence-safe."""
    assert (
        await upgraded_admin_pool.fetchval(
            "SELECT reissue_enabled FROM public.runtime_attention_operator_control WHERE singleton"
        )
        is True
    )
    entry_id = uuid.uuid4()
    episode_id = uuid.uuid4()
    claim_token = uuid.uuid4()
    await upgraded_admin_pool.execute(
        """
        INSERT INTO public.model_catalog (id, alias, runtime_type, model_id)
        VALUES ($1, $2, 'codex', $3)
        """,
        entry_id,
        f"rollback-{entry_id}",
        f"rollback-model-{entry_id}",
    )
    attempt_id = await upgraded_admin_pool.fetchval(
        """
        INSERT INTO public.model_dispatch_attempts (catalog_entry_id, butler, outcome)
        VALUES ($1, 'general', 'runtime_failure') RETURNING id
        """,
        entry_id,
    )
    await upgraded_admin_pool.execute(
        """
        INSERT INTO public.runtime_attention_outbox (
            id, source, lifecycle_state, triggering_attempt_id, source_snapshot, payload,
            claim_token, claim_epoch, delivery_lease_epoch, claimed_by_instance,
            claimed_at, claim_expires_at, delivery_error_class, delivery_error_detail
        ) VALUES (
            $1, 'model_breaker', 'uncertain', $2::bigint,
            jsonb_build_object(
                'catalog_entry_id', $3::text, 'alias', 'rollback-model',
                'model_id', 'rollback-model', 'triggering_attempt_id', $2::bigint,
                'consecutive_failures', 5
            ),
            '{"classification":"model_breaker_open","consecutive_failures":5,"door":"/settings/models"}'::jsonb,
            $4, 1, 1, 'dead-worker', now() - interval '2 minutes',
            now() - interval '1 minute', 'transport_uncertain', 'worker_recovery'
        )
        """,
        episode_id,
        attempt_id,
        str(entry_id),
        claim_token,
    )
    # A genuine normal login and delivery-only Switchboard cannot impersonate
    # the fixed dashboard caller even while the semantic episode is eligible.
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await upgraded_pool.fetchrow(
            "SELECT * FROM public.reissue_runtime_attention_episode($1)", episode_id
        )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _fetchrow_as_role(
            upgraded_admin_pool,
            _SWITCHBOARD,
            "SELECT * FROM public.reissue_runtime_attention_episode($1)",
            episode_id,
        )
    successor = await _fetchrow_as_role(
        upgraded_admin_pool,
        _DASHBOARD,
        "SELECT * FROM public.reissue_runtime_attention_episode($1)",
        episode_id,
    )
    assert successor is not None
    before_attempts = await upgraded_admin_pool.fetchval(
        "SELECT count(*) FROM public.model_dispatch_attempts WHERE catalog_entry_id = $1",
        entry_id,
    )

    # The current operator requires stopped serving/ordinary sessions. This
    # function-scoped pool is closed before the held administrative transition;
    # later calls use the actual fixed dashboard effective role on an
    # administrative connection; this is not native serving admission proof.
    db_url = _NORMAL_POOL_URLS[upgraded_pool]
    await _close_pool(upgraded_pool)
    try:
        _attention_transition(db_url, "reissue", False)
        with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
            await _fetchrow_as_role(
                upgraded_admin_pool,
                _DASHBOARD,
                "SELECT * FROM public.reissue_runtime_attention_episode($1)",
                episode_id,
            )
        assert (
            await upgraded_admin_pool.fetchval(
                "SELECT count(*) FROM public.runtime_attention_outbox WHERE id = $1 OR manual_reissue_of = $1",
                episode_id,
            )
            == 2
        )
        assert (
            await upgraded_admin_pool.fetchval(
                "SELECT count(*) FROM public.model_dispatch_attempts WHERE catalog_entry_id = $1",
                entry_id,
            )
            == before_attempts
        )
    finally:
        _attention_transition(db_url, "reissue", True)
    restored = await _fetchrow_as_role(
        upgraded_admin_pool,
        _DASHBOARD,
        "SELECT * FROM public.reissue_runtime_attention_episode($1)",
        episode_id,
    )
    assert restored is not None
    assert restored["successor_episode_id"] == successor["successor_episode_id"]
    assert (
        await upgraded_admin_pool.fetchval(
            "SELECT count(*) FROM public.runtime_attention_outbox WHERE id=$1 OR manual_reissue_of=$1",
            episode_id,
        )
        == 2
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
@pytest.mark.pg_clock
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
    _selected_upgrade(
        _build_alembic_config(db_url, chains=["core"], target_schema="general"), "core@head"
    )

    # Diagnostic only: no role selection, DDL, explicit transaction or lock.
    # Its independent physical connection is settled before the real upgrade.
    async def pre_second_catalog():
        from butlers.db_authority.grants import compile_function_manifest

        normal = unquote(urlparse(db_url).username or "")
        profiles = compile_deployment_profiles(db_name, source_database="butlers")
        plans = tuple(
            ("core", schema, revision) for schema, revision in _SELECTED_PLANS[db_name].items()
        )
        matches = tuple(
            row
            for row in compile_function_manifest(profiles, normal, plans)
            if (row.schema, row.name, row.signature)
            == (
                "connectors",
                "connectors_filtered_events_ensure_partition",
                "timestamp with time zone",
            )
        )
        if len(matches) != 1:
            return {"status": "UNKNOWN", "facts": {}}
        definition = matches[0]
        connection = await asyncpg.connect(
            _asyncpg_url(_maintenance_url(db_url)), timeout=10, command_timeout=30
        )
        primary = None
        try:
            record = await connection.fetchrow(
                """
                WITH database_row AS (
                    SELECT * FROM pg_catalog.pg_database WHERE datname=current_database()
                ), parent AS (
                    SELECT p.*, owner.rolname AS owner_name FROM pg_catalog.pg_class p
                    JOIN pg_catalog.pg_namespace n ON n.oid=p.relnamespace
                    JOIN pg_catalog.pg_roles owner ON owner.oid=p.relowner
                    WHERE n.nspname='connectors' AND p.relname='filtered_events'
                ), indexes AS (
                    SELECT i.*, idx.relname FROM pg_catalog.pg_index i
                    JOIN pg_catalog.pg_class idx ON idx.oid=i.indexrelid
                    WHERE i.indrelid=(SELECT oid FROM parent)
                ), callable AS (
                    SELECT p.* FROM pg_catalog.pg_proc p
                    WHERE p.oid=pg_catalog.to_regprocedure(
                        'connectors.connectors_filtered_events_ensure_partition(timestamp with time zone)'
                    )
                )
                SELECT current_database()=$1 AS database_matches_selected,
                    COALESCE((SELECT rolsuper AND current_user=session_user
                        FROM pg_catalog.pg_roles WHERE rolname=session_user),false)
                        AS maintenance_actor_qualified,
                    COALESCE((SELECT d.datdba=r.oid FROM database_row d
                        JOIN pg_catalog.pg_roles r ON r.rolname=$2),false)
                        AS normal_login_matches_database_owner,
                    COALESCE((SELECT NOT (rolsuper OR rolcreatedb OR rolcreaterole
                        OR rolreplication OR rolbypassrls OR rolinherit)
                        FROM pg_catalog.pg_roles WHERE rolname=$2),false)
                        AS normal_login_nonprivileged,
                    EXISTS(SELECT FROM parent) AS parent_present,
                    COALESCE((SELECT relkind='p' FROM parent),false)
                        AS parent_kind_partitioned,
                    COALESCE((SELECT owner_name='connector_partition_owner' FROM parent),false)
                        AS parent_owner_fixed_purpose,
                    COALESCE((SELECT p.relowner=d.datdba FROM parent p CROSS JOIN database_row d),false)
                        AS parent_owner_normal_database_owner,
                    EXISTS(SELECT FROM indexes WHERE relname='ix_filtered_events_drain')
                        AS drain_index_present,
                    EXISTS(SELECT FROM indexes WHERE relname='ix_filtered_events_drain'
                        AND indisvalid AND indisready AND NOT indisunique
                        AND indnkeyatts=4 AND indnatts=4 AND indexprs IS NULL AND indpred IS NULL
                        AND indoption[0]=0 AND indoption[1]=0 AND indoption[2]=0 AND indoption[3]=3
                        AND ARRAY(SELECT a.attname::text
                            FROM unnest(indkey::smallint[]) WITH ORDINALITY k(num,ord)
                            JOIN pg_catalog.pg_attribute a ON a.attrelid=indrelid AND a.attnum=k.num
                            ORDER BY k.ord)=ARRAY['connector_type','endpoint_identity','status','received_at'])
                        AS drain_source_keys_options,
                    EXISTS(SELECT FROM indexes WHERE relname='ix_filtered_events_timeline')
                        AS timeline_index_present,
                    EXISTS(SELECT FROM indexes WHERE relname='ix_filtered_events_timeline'
                        AND indisvalid AND indisready AND NOT indisunique
                        AND indnkeyatts=1 AND indnatts=1 AND indexprs IS NULL AND indpred IS NULL
                        AND indoption[0]=3
                        AND ARRAY(SELECT a.attname::text
                            FROM unnest(indkey::smallint[]) WITH ORDINALITY k(num,ord)
                            JOIN pg_catalog.pg_attribute a ON a.attrelid=indrelid AND a.attnum=k.num
                            ORDER BY k.ord)=ARRAY['received_at'])
                        AS timeline_source_keys_options,
                    EXISTS(SELECT FROM callable) AS global_function_present,
                    COALESCE((SELECT owner.rolname=$5 FROM callable p
                        JOIN pg_catalog.pg_roles owner ON owner.oid=p.proowner),false)
                        AS global_function_owner_source,
                    COALESCE((SELECT prosrc=$3 FROM callable),false) AS global_function_body_source,
                    COALESCE((SELECT proconfig=$4::text[] FROM callable),false)
                        AS global_function_settings_source,
                    COALESCE((SELECT prosecdef=$6 FROM callable),false)
                        AS global_function_definer_source,
                    COALESCE((SELECT
                        COALESCE((SELECT array_agg(COALESCE(r.rolname,'PUBLIC')::text ORDER BY
                            COALESCE(r.rolname,'PUBLIC')::text)
                            FROM pg_catalog.aclexplode(COALESCE(p.proacl,
                                pg_catalog.acldefault('f',p.proowner))) a
                            LEFT JOIN pg_catalog.pg_roles r ON r.oid=a.grantee
                            WHERE a.grantee<>p.proowner),ARRAY[]::text[])=$7::text[]
                        AND NOT EXISTS(SELECT FROM pg_catalog.aclexplode(COALESCE(p.proacl,
                            pg_catalog.acldefault('f',p.proowner))) a
                            WHERE a.grantee<>p.proowner AND
                                (a.is_grantable OR a.privilege_type<>'EXECUTE'))
                        FROM callable p),false) AS global_function_acl_source
                """,
                db_name,
                normal,
                definition.body,
                list(definition.settings),
                definition.owner,
                definition.security_definer,
                sorted(set(definition.executors) - {definition.owner}),
            )
            keys = {
                "database_matches_selected",
                "maintenance_actor_qualified",
                "normal_login_matches_database_owner",
                "normal_login_nonprivileged",
                "parent_present",
                "parent_kind_partitioned",
                "parent_owner_fixed_purpose",
                "parent_owner_normal_database_owner",
                "drain_index_present",
                "drain_source_keys_options",
                "timeline_index_present",
                "timeline_source_keys_options",
                "global_function_present",
                "global_function_owner_source",
                "global_function_body_source",
                "global_function_settings_source",
                "global_function_definer_source",
                "global_function_acl_source",
            }
            facts = dict(record) if record is not None else {}
            if set(facts) != keys or any(type(value) is not bool for value in facts.values()):
                return {"status": "UNKNOWN", "facts": {}}
            return {"status": "OBSERVED", "facts": facts}
        except BaseException as error:
            primary = error
            raise
        finally:
            cleanup_error = None
            try:
                await connection.close(timeout=10)
            except BaseException as error:
                cleanup_error = error
            finally:
                try:
                    if not connection.is_closed():
                        connection.terminate()
                except BaseException as error:
                    if cleanup_error is None or (
                        isinstance(cleanup_error, Exception) and not isinstance(error, Exception)
                    ):
                        cleanup_error = error
            if cleanup_error is not None:
                if primary is None or (
                    isinstance(primary, Exception) and not isinstance(cleanup_error, Exception)
                ):
                    raise cleanup_error
                # A pending original exception, especially cancellation, keeps
                # its original traceback while physical cleanup is settled.

    def connector_catalog_snapshot(*, seed=False):
        """Independent native readback; settle its session before chain replay."""
        witness = uuid.UUID("fbc15609-8c13-4f51-bc05-26010c3568e8")
        observer = create_engine(
            _maintenance_url(db_url),
            connect_args={"connect_timeout": 5, "options": "-c statement_timeout=30000"},
        )
        try:
            with observer.begin() as independent:
                if seed:
                    independent.execute(
                        text("""
                        INSERT INTO connectors.filtered_events
                        (id,connector_type,endpoint_identity,external_message_id,
                         source_channel,sender_identity,filter_reason,full_payload)
                        VALUES (:id,'test','serial-replay','serial-replay','test','test',
                                'source-replay','{}'::jsonb)
                    """),
                        {"id": witness},
                    )
                objects = tuple(
                    tuple(row)
                    for row in independent.execute(
                        text("""
                    WITH RECURSIVE tables(oid) AS (
                      SELECT 'connectors.filtered_events'::regclass::oid
                      UNION ALL SELECT i.inhrelid FROM pg_catalog.pg_inherits i
                        JOIN tables t ON t.oid=i.inhparent
                    ), objects AS (
                      SELECT oid FROM tables UNION
                      SELECT indexrelid FROM pg_catalog.pg_index
                        WHERE indrelid IN(SELECT oid FROM tables)
                    )
                    SELECT c.oid,c.relname,c.relkind,r.rolname,c.relrowsecurity,
                      c.relforcerowsecurity,c.relacl::text,c.reloptions,
                      CASE WHEN i.indexrelid IS NULL THEN NULL
                        ELSE pg_catalog.pg_get_indexdef(c.oid) END,
                      i.indisvalid,i.indisready
                    FROM objects o JOIN pg_catalog.pg_class c ON c.oid=o.oid
                    JOIN pg_catalog.pg_roles r ON r.oid=c.relowner
                    LEFT JOIN pg_catalog.pg_index i ON i.indexrelid=c.oid
                    ORDER BY c.oid
                """)
                    )
                )
                function = tuple(
                    independent.execute(
                        text("""
                    SELECT p.oid,r.rolname,p.prosecdef,p.proconfig,p.proacl::text,
                      pg_catalog.encode(pg_catalog.sha256(
                        pg_catalog.convert_to(p.prosrc,'UTF8')),'hex')
                    FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
                    WHERE p.oid=pg_catalog.to_regprocedure(
                      'connectors.connectors_filtered_events_ensure_partition(timestamptz)')
                """)
                    ).one()
                )
                planted = independent.execute(
                    text("""
                    SELECT count(*) FROM connectors.filtered_events
                    WHERE id=:id AND filter_reason='source-replay' AND full_payload='{}'::jsonb
                """),
                    {"id": witness},
                ).scalar_one()
                assert planted == 1
                return objects, function
        finally:
            observer.dispose()

    def owner_lookup_snapshot(selected_url, *, closed=False):
        """Independent source/physical/OID/ACL witness, closed before replay."""
        import hashlib
        from dataclasses import replace

        from butlers.db_authority.grants import compile_function_manifest
        from butlers.db_authority.interfaces import (
            _FUNCTION_ACL,
            _owner_lookup_metadata_matches,
            function_matches,
            inspect_function,
        )

        selected_name = _database(selected_url)
        normal = unquote(urlparse(selected_url).username or "")
        profiles = compile_deployment_profiles(selected_name, source_database="butlers")
        definition = next(
            row
            for row in compile_function_manifest(profiles, normal, (("core", "public", None),))
            if row.identity == ("public", "resolve_owner_triple", "text,text[]")
        )
        if closed:
            definition = replace(definition, executors=())
        observer = create_engine(
            _maintenance_url(selected_url),
            connect_args={
                "connect_timeout": 5,
                "options": "-c statement_timeout=30000",
            },
        )
        try:
            with observer.connect() as independent:
                assert (
                    independent.exec_driver_sql("SELECT current_database()").scalar_one()
                    == selected_name
                )
                actual = inspect_function(independent, definition)
                assert _owner_lookup_metadata_matches(independent, definition)
                assert function_matches(independent, definition, actual)
                acl = tuple(
                    sorted(
                        tuple(row)
                        for row in independent.execute(text(_FUNCTION_ACL), {"oid": actual["oid"]})
                    )
                )
                assert {row[0] for row in acl} == set(definition.executors) - {definition.owner}
                return (
                    actual["oid"],
                    actual["owner"],
                    actual["definer"],
                    tuple(actual["config"] or ()),
                    hashlib.sha256(actual["body"].encode()).hexdigest(),
                    acl,
                )
        finally:
            observer.dispose()

    connector_before_second = connector_catalog_snapshot(seed=True)

    try:
        before_second = asyncio.run(pre_second_catalog())
    except Exception:
        # Observation failure cannot replace or suppress the original upgrade.
        before_second = {"status": "UNKNOWN", "facts": {}}
    try:
        _selected_upgrade(
            _build_alembic_config(db_url, chains=["core"], target_schema="switchboard"), "core@head"
        )
    except Exception as error:
        try:
            from psycopg2 import Error as PostgreSQLError
            from psycopg2 import errors as pg_errors
            from sqlalchemy.exc import InternalError, OperationalError, ProgrammingError

            outer_types = (
                (ProgrammingError, "ProgrammingError"),
                (OperationalError, "OperationalError"),
                (InternalError, "InternalError"),
                (AuthorityError, "AuthorityError"),
            )
            outer = next(
                (label for kind, label in outer_types if isinstance(error, kind)), "UNKNOWN"
            )
            original = error.orig if isinstance(error, DBAPIError) else None
            inner_types = (
                (pg_errors.InsufficientPrivilege, "InsufficientPrivilege"),
                (pg_errors.UndefinedTable, "UndefinedTable"),
                (pg_errors.UndefinedFunction, "UndefinedFunction"),
                (pg_errors.DuplicateTable, "DuplicateTable"),
                (pg_errors.DuplicateFunction, "DuplicateFunction"),
                (pg_errors.InvalidSchemaName, "InvalidSchemaName"),
                (pg_errors.ObjectNotInPrerequisiteState, "ObjectNotInPrerequisiteState"),
                (pg_errors.InvalidTransactionState, "InvalidTransactionState"),
                (pg_errors.SerializationFailure, "SerializationFailure"),
                (pg_errors.DeadlockDetected, "DeadlockDetected"),
            )
            inner = next(
                (label for kind, label in inner_types if isinstance(original, kind)), "UNKNOWN"
            )
            code = (
                getattr(original, "sqlstate", None)
                if isinstance(original, PostgreSQLError)
                else None
            )
            if not (type(code) is str and re.fullmatch(r"[0-9A-Z]{5}", code)):
                code = (
                    getattr(original, "pgcode", None)
                    if isinstance(original, PostgreSQLError)
                    else None
                )
            if not (type(code) is str and re.fullmatch(r"[0-9A-Z]{5}", code)):
                code = "UNKNOWN"
            error.add_note(
                "DB_SERIAL_REPLAY_DIAGNOSTIC "
                + json.dumps(
                    {
                        "version": 1,
                        "phase": "second_core_upgrade",
                        "pre_second_catalog": before_second,
                        "exception_class": outer,
                        "driver_class": inner,
                        "sqlstate": code,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        except Exception:
            pass
        raise

    assert connector_catalog_snapshot() == connector_before_second

    # Actual current replay: compare BEFORE its later finalizer could conceal
    # replacement or ACL activation. The original serial calls stay unchanged.
    current_lookup = owner_lookup_snapshot(db_url)
    with migration_session(_maintenance_url(db_url), source_database="butlers") as held:
        asyncio.run(run_migrations(db_url, chain="core", schema="public", connection=held))
        assert owner_lookup_snapshot(db_url) == current_lookup
        current_plans = tuple(
            ("core", schema, revision) for schema, revision in _SELECTED_PLANS[db_name].items()
        )
        finalize_authority(
            held, source_database="butlers", plans=(*current_plans, ("core", "public", None))
        )
    assert owner_lookup_snapshot(db_url) == current_lookup
    _SELECTED_PLANS[db_name]["public"] = None

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
            assert_at_chain_head(conn, "general")
            assert_at_chain_head(conn, "switchboard")
            from butlers.db_authority.grants import compile_function_manifest

            definitions = compile_function_manifest(
                compile_deployment_profiles(db_name, source_database="butlers"),
                unquote(urlparse(db_url).username or ""),
                (("core", "general", None), ("core", "switchboard", None)),
            )
            for target in ("general", "switchboard"):
                definition = next(
                    row
                    for row in definitions
                    if row.schema == target
                    and row.name == "connectors_filtered_events_ensure_partition"
                )
                assert function_matches(conn, definition, inspect_function(conn, definition))

    finally:
        engine.dispose()

    # Actual bounded-history finalization retains the ordinary parent owner.
    # These are real predecessor upgrades, not fabricated older purpose owners.
    for predecessor, index_name in (
        ("core_230", "ix_filtered_events_id"),
        ("core_244", "ix_filtered_events_status_received"),
    ):
        historical_name = migration_db_name()
        historical_url = create_migration_db(postgres_container, historical_name)
        selected = _build_alembic_config(historical_url, chains=["core"], target_schema="general")
        _selected_upgrade(selected, predecessor)
        with migration_session(_maintenance_url(historical_url), source_database="butlers") as held:
            finalize_authority(
                held, source_database="butlers", plans=(("core", "general", predecessor),)
            )
        independent_engine = create_engine(
            _maintenance_url(historical_url),
            connect_args={"connect_timeout": 5, "options": "-c statement_timeout=30000"},
        )
        try:
            with independent_engine.connect() as independent:
                assert independent.execute(
                    text("""
                    SELECT r.rolname FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_roles r ON r.oid=c.relowner
                    WHERE c.oid='connectors.filtered_events'::regclass
                """)
                ).scalar_one() == unquote(urlparse(historical_url).username or "")
                assert (
                    independent.execute(
                        text("SELECT pg_catalog.to_regclass(:index)"),
                        {"index": "connectors." + index_name},
                    ).scalar_one()
                    is None
                )
                assert (
                    independent.execute(
                        text("SELECT count(*) FROM connectors.filtered_events")
                    ).scalar_one()
                    == 0
                )
            _selected_upgrade(selected, "core@head")
            with independent_engine.connect() as independent:
                assert_at_chain_head(independent, "general")
                assert (
                    independent.execute(
                        text("""
                    SELECT i.indisvalid AND i.indisready FROM pg_catalog.pg_index i
                    WHERE i.indexrelid=pg_catalog.to_regclass(:index)
                """),
                        {"index": "connectors." + index_name},
                    ).scalar_one()
                    is True
                )
                assert (
                    independent.execute(
                        text("""
                    SELECT r.rolname FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_roles r ON r.oid=c.relowner
                    WHERE c.oid='connectors.filtered_events'::regclass
                """)
                    ).scalar_one()
                    == "connector_partition_owner"
                )
                assert (
                    independent.execute(
                        text("SELECT count(*) FROM connectors.filtered_events")
                    ).scalar_one()
                    == 0
                )
        finally:
            independent_engine.dispose()

    # Real mixed administrative history: current core plus Finance018 closes
    # current purpose functions while keeping ordinary relation ownership.
    # No owner/ACL mutation constructs this state and no serving admission is claimed.
    mixed_name = migration_db_name()
    mixed_url = create_migration_db(postgres_container, mixed_name)
    mixed_plans = (
        ("core", "general", None),
        ("core", "switchboard", None),
        ("core", "finance", None),
        ("core", "relationship", None),
        ("relationship", "relationship", None),
        ("finance", "finance", "finance_018"),
    )
    with migration_session(_maintenance_url(mixed_url), source_database="butlers") as held:
        for chain, schema, _ in mixed_plans[:-1]:
            asyncio.run(run_migrations(mixed_url, chain=chain, schema=schema, connection=held))
        _upgrade_chain_to_revision(
            mixed_url, chain="finance", schema="finance", revision="finance_018", connection=held
        )
        finalize_authority(held, source_database="butlers", plans=mixed_plans)
    mixed_engine = create_engine(
        _maintenance_url(mixed_url),
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=30000"},
    )
    try:
        from dataclasses import replace

        from butlers.db_authority.grants import compile_function_manifest

        normal = unquote(urlparse(mixed_url).username or "")
        definition = next(
            replace(row, executors=())
            for row in compile_function_manifest(
                compile_deployment_profiles(mixed_name, source_database="butlers"),
                normal,
                mixed_plans,
            )
            if (row.schema, row.name)
            == ("connectors", "connectors_filtered_events_ensure_partition")
        )
        with mixed_engine.connect() as independent:
            before_function = dict(inspect_function(independent, definition))
            assert function_matches(independent, definition, before_function)
            assert (
                independent.execute(
                    text(
                        "SELECT pg_catalog.pg_get_userbyid(relowner) "
                        "FROM pg_catalog.pg_class WHERE oid='connectors.filtered_events'::regclass"
                    )
                ).scalar_one()
                == normal
            )
            before_parent = independent.execute(
                text(
                    "SELECT oid FROM pg_catalog.pg_class "
                    "WHERE oid='connectors.filtered_events'::regclass"
                )
            ).scalar_one()
        verifier_before = _runtime_probe_verifier_snapshot(mixed_url, seed=True, closed=True)
        closed_lookup = owner_lookup_snapshot(mixed_url, closed=True)
        # The first historical finalizer above genuinely produces closed
        # Relationship current functions/relations. Its real rel037 inverse
        # now tests age independently of the administrative zero-rights phase.
        from alembic import command
        from butlers.testing.migration import run_test_alembic_command

        mixed_relationship_config = _build_alembic_config(
            mixed_url, chains=["relationship"], target_schema="relationship"
        )
        run_test_alembic_command(
            postgres_container, command.downgrade, mixed_relationship_config, "rel_036"
        )
        with mixed_engine.connect() as independent:
            assert (
                independent.execute(
                    text(
                        "SELECT count(*)=1 FROM relationship.alembic_version WHERE version_num='rel_036'"
                    )
                ).scalar_one()
                is True
            )
        assert owner_lookup_snapshot(mixed_url, closed=True) == closed_lookup
        # public is an admitted administrative target, absent from mixed_plans.
        with migration_session(_maintenance_url(mixed_url), source_database="butlers") as held:
            asyncio.run(run_migrations(mixed_url, chain="core", schema="public", connection=held))
            assert _runtime_probe_verifier_snapshot(mixed_url, closed=True) == verifier_before
            assert owner_lookup_snapshot(mixed_url, closed=True) == closed_lookup
            # Restore the real selected head on this SAME held session before
            # the original complete historical finalizer; no second lock/session.
            asyncio.run(
                run_migrations(
                    mixed_url, chain="relationship", schema="relationship", connection=held
                )
            )
            finalize_authority(
                held, source_database="butlers", plans=(*mixed_plans, ("core", "public", None))
            )
        with mixed_engine.connect() as independent:
            assert_at_chain_head(independent, "public")
            assert _runtime_probe_verifier_snapshot(mixed_url, closed=True) == verifier_before
            assert owner_lookup_snapshot(mixed_url, closed=True) == closed_lookup
            assert dict(inspect_function(independent, definition)) == before_function
            assert function_matches(
                independent, definition, inspect_function(independent, definition)
            )
            assert (
                independent.execute(
                    text(
                        "SELECT oid FROM pg_catalog.pg_class "
                        "WHERE oid='connectors.filtered_events'::regclass"
                    )
                ).scalar_one()
                == before_parent
            )
    finally:
        mixed_engine.dispose()


def _runtime_probe_verifier_snapshot(db_url: str, *, seed=False, closed=False):
    """Independent settled native catalog/data witness; never serving admission."""
    from dataclasses import replace

    from butlers.db_authority.grants import compile_function_manifest
    from butlers.db_authority.interfaces import _FUNCTION_ACL

    normal = unquote(urlparse(db_url).username or "")
    definition = next(
        row
        for row in compile_function_manifest(
            compile_deployment_profiles(_database(db_url), source_database="butlers"),
            normal,
            (("core", "general", None),),
        )
        if row.name == "record_runtime_probe_verification"
    )
    if closed:
        definition = replace(definition, executors=())
    witness_id = uuid.UUID("63c3bb2b-dbc6-48c1-96b5-6c4026786293")
    engine = create_engine(
        _maintenance_url(db_url),
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=30000"},
    )
    try:
        with engine.begin() as independent:
            if seed:
                independent.execute(
                    text(
                        "INSERT INTO public.model_catalog(id,alias,runtime_type,model_id) "
                        "VALUES(:id,'source-verifier-replay-witness','codex','source-only')"
                    ),
                    {"id": witness_id},
                )
            actual = dict(inspect_function(independent, definition))
            assert function_matches(independent, definition, actual)
            acl = tuple(
                sorted(
                    tuple(row.values())
                    for row in independent.execute(
                        text(_FUNCTION_ACL), {"oid": actual["oid"]}
                    ).mappings()
                )
            )
            planted = dict(
                independent.execute(
                    text(
                        "SELECT id,alias,runtime_type,model_id,enabled,priority,last_verified_at,"
                        "last_verified_latency_ms,last_verified_ok,last_verified_error "
                        "FROM public.model_catalog WHERE id=:id"
                    ),
                    {"id": witness_id},
                )
                .mappings()
                .one()
            )
            assert planted["alias"] == "source-verifier-replay-witness"
            return actual, acl, planted
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
        _selected_upgrade(
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
                assert_at_chain_head(conn, target_schema)
    finally:
        engine.dispose()

    assert _has_exact_finalized_runtime_attention_interface(db_url)

    verifier_before = _runtime_probe_verifier_snapshot(db_url, seed=True)
    _selected_upgrade(
        _build_alembic_config(db_url, chains=["core"], target_schema="public"), "core@head"
    )
    assert _runtime_probe_verifier_snapshot(db_url) == verifier_before


def test_core_chain_serializes_global_runtime_attention_downgrade_and_reapply_across_processes(
    postgres_container,
) -> None:
    """Current unprepared reverse refuses before stamp or protected effects."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    target_schemas = ("general", "switchboard")
    for target_schema in target_schemas:
        _selected_upgrade(
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
    downgrade_diagnostics = "\n".join(
        f"{target_schema}: returncode={returncode}\n{stderr}"
        for target_schema, (returncode, _stdout, stderr) in downgrade_results.items()
    )
    assert set(failed_downgrades) == set(target_schemas), downgrade_diagnostics
    assert all(
        "Explicit preserving operator reverse is required" in stderr
        for stderr in failed_downgrades.values()
    ), downgrade_diagnostics

    engine = create_engine(bootstrap_url)
    try:
        with engine.connect() as conn:
            for target_schema in target_schemas:
                # The current-head preflight must refuse before core_231's
                # autocommit downgrade can commit any newer migration or stamp.
                # Derive the head so a later serial migration does not turn this
                # rollback-safety assertion into a stale revision literal.
                assert conn.execute(
                    text(f"SELECT version_num FROM {_quote_ident(target_schema)}.alembic_version")
                ).scalar_one() == get_chain_head("core")
            for relation in (
                "public.runtime_attention_outbox",
                "public.runtime_attention_delivery_lease",
                "public.idx_model_dispatch_attempts_catalog_ts_id",
                "public.idx_model_dispatch_attempts_outcome_ts_id",
            ):
                assert conn.execute(
                    text(f"SELECT to_regclass('{relation}') IS NOT NULL")
                ).scalar_one()
    finally:
        engine.dispose()
    assert _has_bootstrap_finalized_runtime_attention_interface(bootstrap_url)

    reapply_results = _run_concurrent_core_head_upgrades(db_url, target_schemas)
    failed_reapplications = {
        target_schema: stderr
        for target_schema, (returncode, _stdout, stderr) in reapply_results.items()
        if returncode != 0
    }
    assert not failed_reapplications, "\n".join(failed_reapplications.values())

    engine = create_engine(bootstrap_url)
    try:
        with engine.connect() as conn:
            for target_schema in target_schemas:
                assert_at_chain_head(conn, target_schema)
    finally:
        engine.dispose()
    assert _has_bootstrap_finalized_runtime_attention_interface(bootstrap_url)

    verifier_before = _runtime_probe_verifier_snapshot(db_url, seed=True)
    _selected_upgrade(
        _build_alembic_config(db_url, chains=["core"], target_schema="public"), "core@head"
    )
    assert _runtime_probe_verifier_snapshot(db_url) == verifier_before


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

        with pytest.raises(DBAPIError, match="already exists"):
            _selected_upgrade(_build_alembic_config(db_url, chains=["core"]), "core_198")

        with engine.connect() as conn:
            assert conn.execute(
                text(f"SELECT to_regclass('public.{index_name}') IS NOT NULL")
            ).scalar_one()
            assert conn.execute(
                text("SELECT to_regclass('public.runtime_attention_outbox') IS NULL")
            ).scalar_one()

        # A real current install rolled back in its own transaction owns no
        # durable indexes. This is installer atomicity, not operator reversal.
        with engine.begin() as conn:
            conn.execute(text(f"DROP INDEX public.{index_name}"))
        _empty_install_rollback(db_url)
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
        with pytest.raises(DBAPIError, match="already exists"):
            _selected_upgrade(_build_alembic_config(db_url, chains=["core"]), "core_198")
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
        _selected_upgrade(
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
    assert all("already exists" in stderr for stderr in failed_processes.values())

    engine = create_engine(bootstrap_url)
    try:
        with engine.connect() as conn:
            for target_schema in target_schemas:
                # pinned-revision: the poisoned head upgrade must fail, leaving
                # both schemas stamped at the pre-core_198 boundary.
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
        _selected_upgrade(
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
    assert all("already exists" in stderr for stderr in failed_processes.values())
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
        _selected_upgrade(
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
    assert all("already exists" in stderr for stderr in failed_processes.values())
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
        engine = create_engine(
            bootstrap_url if role is not None else db_url, isolation_level="AUTOCOMMIT"
        )
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

    # The selected normal migration login is not a runtime identity. Unset
    # normal identity must not become a producer through a shared-login fallback.
    with pytest.raises(DBAPIError) as unset_producer:
        execute_as(None, "SELECT public.append_runtime_attention_model_breaker(:trigger_id)")
    assert getattr(unset_producer.value.orig, "pgcode", None) == "42501"

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

    # Actual owner EXECUTE reaches the allowlist guard without an artificial
    # fixture grant. PUBLIC-only has no authority; the purpose owner is still
    # not a canonical runtime producer even though it owns the callable.
    public_only_role = f"runtime_attention_public_only_{db_name}"
    admin = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f"CREATE ROLE {_quote_ident(public_only_role)} NOLOGIN"))
            conn.execute(text(f"SET ROLE {_quote_ident(public_only_role)}"))
            try:
                with pytest.raises(DBAPIError):
                    conn.execute(
                        text("SELECT public.append_runtime_attention_model_breaker(:trigger_id)"),
                        {"trigger_id": trigger_id},
                    )
            finally:
                conn.execute(text("RESET ROLE"))
            conn.execute(text("SET ROLE runtime_attention_outbox_owner"))
            try:
                with pytest.raises(DBAPIError, match="active canonical SET ROLE"):
                    conn.execute(
                        text("SELECT public.append_runtime_attention_model_breaker(:trigger_id)"),
                        {"trigger_id": trigger_id},
                    )
            finally:
                conn.execute(text("RESET ROLE"))
    finally:
        admin.dispose()

    # Switchboard alone gets direct read/claim access. The native normal login
    # is separately refused; the administrative SET ROLE probe is not native
    # dedicated-login admission evidence.
    assert execute_as(_SWITCHBOARD, f"SELECT count(*) FROM {_OUTBOX}") == 1
    with pytest.raises(DBAPIError) as unset_reader:
        execute_as(None, f"SELECT count(*) FROM {_OUTBOX}")
    assert getattr(unset_reader.value.orig, "pgcode", None) == "42501"


def test_empty_outbox_can_disable_and_reenable_producers_through_bootstrap(
    postgres_container,
) -> None:
    """Current preserving disable/re-enable retains objects, stamps and evidence."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    _upgrade_to_core_head(db_url)
    config = _build_alembic_config(bootstrap_url, chains=["core"])
    _attention_transition(db_url, "producers", False)
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
            assert not conn.execute(
                text(
                    "SELECT producers_enabled FROM "
                    "public.runtime_attention_producer_control WHERE singleton"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    _attention_transition(db_url, "producers", True)
    _selected_upgrade(config, "core@head")
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
                    "SELECT producers_enabled FROM "
                    "public.runtime_attention_producer_control WHERE singleton"
                )
            ).scalar_one()
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

    assert isinstance(_validated_current_episode(db_url), uuid.UUID)
    assert isinstance(_validated_current_episode(db_url, fleet=True), uuid.UUID)


def test_nonempty_outbox_survives_a_refused_core_197_downgrade(
    postgres_container,
) -> None:
    """REQ-runtime-attention-outbox-001: current reverse refuses before effects.

    The actual current-head Alembic direction guard requires the source-owned
    preserving R0/R1 path before crossing core281. It cannot tear down or restamp
    this populated outbox. Bounded core198 separately proves its direct protected
    boundary guard; neither refusal substitutes for positive R0/R1 proof.
    """
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    _upgrade_to_core_head(db_url)
    engine = create_engine(bootstrap_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(_INSERT_FLEET_HALT_EVIDENCE_SQL))
    finally:
        engine.dispose()

    config = _build_alembic_config(bootstrap_url, chains=["core"])
    with pytest.raises(AuthorityError, match="preserving operator reverse"):
        _selected_downgrade(config, "core_197")

    engine = create_engine(bootstrap_url)
    try:
        with engine.connect() as conn:
            assert_at_chain_head(conn)
            assert conn.execute(text(f"SELECT count(*) FROM {_OUTBOX}")).scalar_one() == 1
            assert conn.execute(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'runtime_config' "
                    "AND column_name = 'core_groups_narrowing_reason'"
                    ")"
                )
            ).scalar_one()
    finally:
        engine.dispose()


def _run_bootstrap_rollback(engine: Engine) -> None:
    """Drive the actual preserving transition through a separate held session."""
    _attention_transition(engine.url.render_as_string(hide_password=False), "producers", False)


def test_core_198_downgrade_refuses_durable_evidence_and_names_forward_remediation(
    postgres_container,
) -> None:
    """Current boundary refusal preserves both durable relations and the stamp.

    The stored teardown/forward-remediation API is retired. The source-owned
    pre-autocommit boundary guard now refuses the same destructive request;
    positive preserving R0/R1 remains a separate real operator carrier.
    """
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    _selected_upgrade(_build_alembic_config(db_url, chains=["core"]), "core_198")

    def bounded_catalog_oids():
        database = _database(db_url)
        plans = tuple(
            ("core", schema, revision) for schema, revision in _SELECTED_PLANS[database].items()
        )
        with migration_session(_maintenance_url(db_url), source_database="butlers") as held:
            normal = unquote(urlparse(_NORMAL_URLS[database]).username or "")
            profiles = compile_deployment_profiles(database, source_database="butlers")
            definitions = _source_attention_definitions(profiles, normal, plans)
            assert definitions
            assert all(not row.executors for row in definitions)
            assert all(
                function_matches(held, row, inspect_function(held, row)) for row in definitions
            )
            return tuple(inspect_function(held, row)["oid"] for row in definitions)

    before_catalog_oids = bounded_catalog_oids()
    engine = create_engine(bootstrap_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(_INSERT_FLEET_HALT_EVIDENCE_SQL))
    finally:
        engine.dispose()

    config = _build_alembic_config(bootstrap_url, chains=["core"])
    with pytest.raises(AuthorityError, match="Protected boundary remains installed"):
        _selected_downgrade(config, "core_197")

    engine = create_engine(bootstrap_url)
    try:
        with engine.connect() as conn:
            # The refusal is a refusal, not a partial teardown.
            assert conn.execute(text(f"SELECT count(*) FROM {_OUTBOX}")).scalar_one() == 1
            assert conn.execute(
                text("SELECT to_regclass('public.runtime_attention_delivery_lease') IS NOT NULL")
            ).scalar_one()
    finally:
        engine.dispose()
    assert bounded_catalog_oids() == before_catalog_oids
    assert _has_bootstrap_finalized_runtime_attention_interface(bootstrap_url)


def _await_blocked_outbox_teardown_lock(engine: Engine, timeout: float = 30.0) -> None:
    """Block until the rollback is provably queued for the teardown lock."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with engine.connect() as conn:
            if conn.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_locks
                        WHERE NOT granted
                          AND locktype = 'relation'
                          AND mode = 'AccessExclusiveLock'
                          AND relation = to_regclass('public.runtime_attention_outbox')
                    )
                    """
                )
            ).scalar_one():
                return
        time.sleep(0.05)
    raise AssertionError("bootstrap rollback never queued for the outbox teardown lock")


def test_bootstrap_rollback_refuses_evidence_committed_while_it_waits_for_the_lock(
    postgres_container,
) -> None:
    """An actual waiting operator retains the writer committed behind its lock.

    The retired teardown is replaced by a preserving disable. This keeps the
    real concurrent SQL interleaving and then independently refuses destructive
    reverse; a pre-lock empty snapshot cannot justify deleting the new row.
    """
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    _upgrade_to_core_head(db_url)
    engine = create_engine(bootstrap_url)
    try:
        writer = engine.connect()
        writer.execute(text(_INSERT_FLEET_HALT_EVIDENCE_SQL))
        with ThreadPoolExecutor(max_workers=1) as pool:
            rollback = pool.submit(_run_bootstrap_rollback, engine)
            try:
                _await_blocked_outbox_teardown_lock(engine)
                writer.commit()
                rollback.result(timeout=60)
            finally:
                writer.close()
        config = _build_alembic_config(db_url, chains=["core"])
        with pytest.raises(AuthorityError, match="preserving operator reverse"):
            _selected_downgrade(config, "core_197")
        with engine.connect() as conn:
            assert conn.execute(text(f"SELECT count(*) FROM {_OUTBOX}")).scalar_one() == 1
            assert conn.execute(
                text("SELECT to_regclass('public.runtime_attention_delivery_lease') IS NOT NULL")
            ).scalar_one()
            assert_at_chain_head(conn)
            assert conn.execute(
                text(
                    "SELECT NOT producers_enabled FROM public.runtime_attention_producer_control "
                    "WHERE singleton"
                )
            ).scalar_one()
    finally:
        engine.dispose()


def test_init_db_rerun_adopts_the_renamed_debounce_marker_planter(
    postgres_container,
) -> None:
    """Retired-name successor: current bootstrap never revives either planter.

    An actual old-style writer still inserts both dispatch outcomes. It creates
    no debounce audit row, while the validated current producer remains live.
    This explicitly retires rename/OID adoption, not the successful writer.
    """
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    _upgrade_to_core_head(db_url)
    _rerun_actual_init_db(bootstrap_url, db_url)
    admin = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            assert conn.execute(
                text(
                    "SELECT to_regprocedure('public.runtime_attention_plant_legacy_debounce_marker()') "
                    "IS NULL"
                )
            ).scalar_one()
            assert conn.execute(
                text(
                    "SELECT to_regprocedure('public.runtime_attention_legacy_producer_fence()') IS NULL"
                )
            ).scalar_one()
            assert conn.execute(
                text("""
                SELECT NOT EXISTS (SELECT FROM pg_trigger WHERE NOT tgisinternal
                  AND tgrelid='public.model_dispatch_attempts'::regclass
                  AND tgname IN ('runtime_attention_plant_legacy_debounce_marker_trigger',
                                 'runtime_attention_legacy_producer_fence_trigger'))
            """)
            ).scalar_one()
            entry_id = uuid.uuid4()
            _plant_marker_for_legacy_writer(conn, entry_id, "rename-retired-writer")
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM public.model_dispatch_attempts WHERE catalog_entry_id=:id"
                    ),
                    {"id": entry_id},
                ).scalar_one()
                == 2
            )
            assert _marker_rows(conn, entry_id) == []
    finally:
        admin.dispose()
    assert isinstance(_validated_current_episode(db_url), uuid.UUID)
    assert isinstance(_validated_current_episode(db_url, fleet=True), uuid.UUID)
    assert _has_exact_finalized_runtime_attention_interface(db_url)


_LEGACY_MARKER_ACTOR = "runtime_attention_cutover_fence"


def _plant_marker_for_legacy_writer(conn, entry_id: uuid.UUID, alias: str) -> None:
    """Execute both old-style writer INSERTs without an ABI annotation.

    Current source has retired the debounce planter. Both real INSERTs must
    still commit; the owning cases separately assert no new legacy marker and
    positive validated current producers. No old trigger execution is claimed.
    """
    conn.execute(
        text(
            """
            INSERT INTO public.model_catalog (id, alias, runtime_type, model_id)
            VALUES (:id, :alias, 'codex', :alias)
            """
        ),
        {"id": entry_id, "alias": alias},
    )
    conn.execute(text(f"SET ROLE {_quote_ident(_MODEL_PRODUCER)}"))
    try:
        conn.execute(
            text(
                """
                INSERT INTO public.model_dispatch_attempts (
                    catalog_entry_id, butler, outcome, failure_reason
                ) VALUES (:id, 'general', 'runtime_failure', 'legacy writer')
                """
            ),
            {"id": entry_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO public.model_dispatch_attempts (
                    catalog_entry_id, butler, outcome, failure_reason
                ) VALUES (
                    :id, 'general', 'quota_skip',
                    'Monthly spend ceiling reached: legacy writer'
                )
                """
            ),
            {"id": entry_id},
        )
    finally:
        conn.execute(text("RESET ROLE"))


def _marker_rows(conn, entry_id: uuid.UUID) -> list[tuple[str, str, str]]:
    return [
        (row[0], row[1], row[2])
        for row in conn.execute(
            text(
                """
                SELECT actor, action, note
                FROM public.audit_log
                WHERE target IN (:breaker_target, 'ceiling_halt')
                ORDER BY id
                """
            ),
            {"breaker_target": f"model_breaker:{entry_id}"},
        ).all()
    ]


def test_init_db_rerun_converges_the_debounce_marker_audit_vocabulary(
    postgres_container,
) -> None:
    """Current retirement preserves immutable old vocabulary without re-planting.

    Synthetic historical text is written through real audit.append under the
    actual General role. It is not evidence that the retired planter ran. The
    new source converges only current interfaces and preserves these rows.
    """
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    _upgrade_to_core_head(db_url)
    legacy_entry_id = uuid.uuid4()
    receipts = _append_synthetic_legacy_history(db_url, legacy_entry_id)
    admin = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            before = _marker_rows(conn, legacy_entry_id)
            assert before == [
                (_LEGACY_MARKER_ACTOR, "model_breaker_open_notified", "blocked_old_binary"),
                (_LEGACY_MARKER_ACTOR, "ceiling_halt_notified", "synthetic retained legacy month"),
            ]
            writers = conn.execute(
                text("SELECT id,writer_role FROM public.audit_log WHERE id=ANY(:ids) ORDER BY id"),
                {"ids": receipts},
            ).all()
            assert writers == [(receipt, _MODEL_PRODUCER) for receipt in receipts]
            retained_rows = conn.execute(
                text(
                    "SELECT to_jsonb(history)::text FROM public.audit_log AS history "
                    "WHERE id=ANY(:ids) ORDER BY id"
                ),
                {"ids": receipts},
            ).all()
            assert len(retained_rows) == 2
            assert conn.execute(
                text(
                    "SELECT to_regprocedure('public.runtime_attention_plant_legacy_debounce_marker()') "
                    "IS NULL"
                )
            ).scalar_one()
    finally:
        admin.dispose()
    _rerun_actual_init_db(bootstrap_url, db_url)
    _rerun_actual_init_db(bootstrap_url, db_url)
    admin = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            assert _marker_rows(conn, legacy_entry_id) == before
            assert (
                conn.execute(
                    text(
                        "SELECT to_jsonb(history)::text FROM public.audit_log AS history "
                        "WHERE id=ANY(:ids) ORDER BY id"
                    ),
                    {"ids": receipts},
                ).all()
                == retained_rows
            )
            assert (
                conn.execute(
                    text(
                        "SELECT id,writer_role FROM public.audit_log WHERE id=ANY(:ids) ORDER BY id"
                    ),
                    {"ids": receipts},
                ).all()
                == writers
            )
            entry_id = uuid.uuid4()
            _plant_marker_for_legacy_writer(conn, entry_id, "post-retirement")
            # Only the deliberately retained historical fleet row is visible;
            # no current legacy writer adds or rewrites a debounce marker.
            assert _marker_rows(conn, entry_id) == [before[1]]
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM public.model_dispatch_attempts WHERE catalog_entry_id=:id"
                    ),
                    {"id": entry_id},
                ).scalar_one()
                == 2
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM public.audit_log WHERE actor=:actor "
                        "AND note='blocked_old_binary'"
                    ),
                    {"actor": _LEGACY_MARKER_ACTOR},
                ).scalar_one()
                == 1
            )
            assert conn.execute(
                text(
                    "SELECT to_regprocedure('public.runtime_attention_plant_legacy_debounce_marker()') "
                    "IS NULL"
                )
            ).scalar_one()
    finally:
        admin.dispose()
    assert isinstance(_validated_current_episode(db_url), uuid.UUID)
    assert isinstance(_validated_current_episode(db_url, fleet=True), uuid.UUID)
    assert _has_exact_finalized_runtime_attention_interface(db_url)


_FLEET_HALT_DEFINITION_SQL = (
    "SELECT pg_get_functiondef('public.append_runtime_attention_fleet_halt()'::regprocedure)"
)
_FLEET_HALT_OID_SQL = "SELECT 'public.append_runtime_attention_fleet_halt()'::regprocedure::oid"

# The declaration only, never the whole body: the body also *names*
# clock_timestamp, in the comment explaining why it does not read it.
_FLEET_HALT_V_MONTH = re.compile(r"v_month\s+DATE\s*:=\s*(.+?);")


def _fleet_halt_month_source(definition: str) -> str:
    declaration = _FLEET_HALT_V_MONTH.search(definition)
    assert declaration, "the fleet-halt producer no longer declares v_month"
    return declaration.group(1)


# The exact v2 body a database upgraded before bu-jxelx carries: v_month read
# from clock_timestamp, everything else identical.  Restoring it with CREATE OR
# REPLACE keeps the OID, so the regressed database is the real pre-fix shape
# rather than a look-alike installed alongside it.
_CLOCK_TIMESTAMP_FLEET_HALT_BODY = """
CREATE OR REPLACE FUNCTION public.append_runtime_attention_fleet_halt()
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $clock_timestamp_fleet_halt$
DECLARE
    v_month DATE := date_trunc('month', clock_timestamp() AT TIME ZONE 'UTC')::date;
    v_denied_count INTEGER;
    v_first_denied_at TIMESTAMPTZ;
    v_episode_id UUID;
    v_enabled BOOLEAN;
    v_activated_at TIMESTAMPTZ;
BEGIN
    IF COALESCE(current_setting('role', true), '') <> ALL (ARRAY[
        'butler_chronicler_rw', 'butler_education_rw', 'butler_finance_rw',
        'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
        'butler_lifestyle_rw', 'butler_messenger_rw', 'butler_qa_rw',
        'butler_relationship_rw', 'butler_switchboard_rw', 'butler_travel_rw'
    ]) THEN
        RAISE EXCEPTION 'runtime-attention producer requires an active canonical SET ROLE'
            USING ERRCODE = '42501';
    END IF;
    SELECT producers_enabled, producer_activated_at
    INTO v_enabled, v_activated_at
    FROM public.runtime_attention_producer_control
    WHERE singleton;
    IF NOT COALESCE(v_enabled, false) THEN
        RETURN NULL;
    END IF;

    SELECT count(*)::integer, min(ts)
    INTO v_denied_count, v_first_denied_at
    FROM public.model_dispatch_attempts
    WHERE outcome = 'quota_skip'
      AND left(COALESCE(failure_reason, ''), length('Monthly spend ceiling reached'))
            = 'Monthly spend ceiling reached'
      AND date_trunc('month', ts AT TIME ZONE 'UTC')::date = v_month;
    IF v_denied_count < 1 THEN
        RAISE EXCEPTION 'runtime-attention fleet-halt trigger lacks current-month ceiling evidence'
            USING ERRCODE = '23514';
    END IF;
    IF v_activated_at IS NULL OR v_first_denied_at < v_activated_at THEN
        RETURN NULL;
    END IF;
    PERFORM pg_advisory_xact_lock(
        hashtextextended('runtime_attention_fleet_halt:' || v_month::text, 0)
    );
    INSERT INTO public.runtime_attention_outbox (
        source, fleet_halt_month, source_snapshot, payload
    )
    VALUES (
        'fleet_halt',
        v_month,
        jsonb_build_object(
            'month', v_month::text,
            'denied_count', v_denied_count,
            'first_denied_at', v_first_denied_at
        ),
        jsonb_build_object(
            'classification', 'monthly_spend_ceiling',
            'door', '/spend?openDrawer=fleet-halt'
        )
    )
    ON CONFLICT (fleet_halt_month)
        WHERE source = 'fleet_halt' AND fleet_halt_month IS NOT NULL
        DO NOTHING
    RETURNING id INTO v_episode_id;
    IF v_episode_id IS NULL THEN
        SELECT id INTO v_episode_id
        FROM public.runtime_attention_outbox
        WHERE source = 'fleet_halt' AND fleet_halt_month = v_month;
    END IF;
    RETURN v_episode_id;
END;
$clock_timestamp_fleet_halt$;
"""


def test_init_db_rerun_converges_the_fleet_halt_month_clock(postgres_container) -> None:
    """bu-jxelx: current finalization restores the transaction-stable month.

    Regress only the actual producer body, retaining its identity. The real
    selected held-maintenance finalizer must restore the exact source body
    without recreating a retired stored installer or replacing the function.
    """
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    _upgrade_to_core_head(db_url)

    admin = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            fresh_definition = conn.execute(text(_FLEET_HALT_DEFINITION_SQL)).scalar_one()
            assert "clock_timestamp()" not in _fleet_halt_month_source(fresh_definition), (
                "a fresh bootstrap still derives the fleet-halt month from the statement "
                "clock; the advisory lock the recorder takes at BEGIN cannot match it"
            )
            producer_oid = conn.execute(text(_FLEET_HALT_OID_SQL)).scalar_one()

            conn.exec_driver_sql(_CLOCK_TIMESTAMP_FLEET_HALT_BODY)
            assert conn.execute(text(_FLEET_HALT_OID_SQL)).scalar_one() == producer_oid, (
                "restoring the pre-fix body replaced the producer instead of rewriting it, "
                "so the object under test is no longer the one the interface exposes"
            )
            assert "clock_timestamp()" in _fleet_halt_month_source(
                conn.execute(text(_FLEET_HALT_DEFINITION_SQL)).scalar_one()
            ), "the pre-fix month source was not actually restored"
    finally:
        admin.dispose()

    _rerun_actual_init_db(bootstrap_url, db_url)

    admin = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            assert conn.execute(text(_FLEET_HALT_OID_SQL)).scalar_one() == producer_oid, (
                "the rerun replaced the producer instead of rewriting it in place"
            )
            assert (
                conn.execute(text(_FLEET_HALT_DEFINITION_SQL)).scalar_one() == fresh_definition
            ), "a migrated database and a fresh bootstrap disagree on the producer body"
    finally:
        admin.dispose()


# ---------------------------------------------------------------------------
# REQ-butler-control-plane-liveness-007: fleet and overdue-QA condition attention
# ---------------------------------------------------------------------------

_APPEND_CONDITION = "SELECT public.append_runtime_attention_condition($1)"
_CONDITION_OUTBOX_COUNT = (
    "SELECT count(*) FROM public.runtime_attention_outbox WHERE source = 'control_plane_condition'"
)


def _asyncpg_url(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql://", 1)


def _fresh_condition_database(postgres_container) -> tuple[str, str]:
    """A fresh core-head database with the v4 condition interface installed."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    _upgrade_to_core_head(db_url)
    return db_url, migration_bootstrap_db_url(postgres_container, db_name)


async def _insert_condition(
    admin: asyncpg.Pool,
    source: str,
    fingerprint: str,
    *,
    age: timedelta = timedelta(minutes=10),
    state: str = "open",
    episode: int = 1,
) -> uuid.UUID:
    resolved = state == "resolved"
    return await admin.fetchval(
        """
        INSERT INTO public.infra_conditions (
            source, fingerprint, episode, state, first_detected_at, last_confirmed_at,
            resolved_at, recovered_after_s, summary, metadata
        ) VALUES (
            $1, $2, $3, $4, now() - $5::interval, now(),
            CASE WHEN $6 THEN now() END, CASE WHEN $6 THEN 1.0 END,
            'general, health: 127.0.0.1:41101 refused', '{"affected": [{"name": "general"}]}'
        ) RETURNING id
        """,
        source,
        fingerprint,
        episode,
        state,
        age,
        resolved,
    )


def test_condition_producer_appends_one_safe_episode_per_condition_even_after_retention(
    postgres_container,
) -> None:
    """REQ-butler-control-plane-liveness-007: one idempotent, content-blind episode.

    Grace, concurrent producers, the fixed identity allowlist, the Switchboard-only
    producer grant, the CHECK/unique backstops against a forged row, and the
    condition-side marker that outlives outbox retention -- all on real PostgreSQL.
    """
    from butlers.core.fleet_conditions import (
        FLEET_FINGERPRINT,
        FLEET_SOURCE,
        QA_PATROL_OVERDUE_FINGERPRINT,
        QA_PATROL_SOURCE,
        QA_PATROL_STOPPED_FINGERPRINT,
    )

    db_url, bootstrap_url = _fresh_condition_database(postgres_container)

    async def scenario() -> None:
        admin = await asyncpg.create_pool(
            _asyncpg_url(bootstrap_url), min_size=1, max_size=2, init=_decode_jsonb
        )
        pool = await asyncpg.create_pool(
            _asyncpg_url(db_url), min_size=1, max_size=8, init=_decode_jsonb
        )
        _ROLE_CONTROL_URLS[pool] = _asyncpg_url(bootstrap_url)
        try:
            fleet = await _insert_condition(admin, FLEET_SOURCE, FLEET_FINGERPRINT)
            qa = await _insert_condition(
                admin, QA_PATROL_SOURCE, QA_PATROL_OVERDUE_FINGERPRINT, age=timedelta(0)
            )
            stopped = await _insert_condition(
                admin, QA_PATROL_SOURCE, QA_PATROL_STOPPED_FINGERPRINT
            )
            resolved = await _insert_condition(
                admin, FLEET_SOURCE, FLEET_FINGERPRINT, state="resolved", episode=2
            )
            unrelated = await _insert_condition(admin, "infra_state", "not-a-paging-identity")

            # The migration login can CREATE in public.  A better-matching
            # overload planted there must never run inside the v4 definers,
            # which execute as runtime_attention_outbox_owner.
            await pool.execute(
                """
                CREATE FUNCTION public.hashtextextended(text, integer) RETURNS bigint
                LANGUAGE plpgsql AS $hijack$
                BEGIN
                    RAISE EXCEPTION 'hijacked as %', current_user;
                END;
                $hijack$
                """
            )
            # Concurrent controller scans converge on one episode.
            ids = await asyncio.gather(
                *(_call_as_role(pool, _SWITCHBOARD, _APPEND_CONDITION, fleet) for _ in range(8))
            )
            assert len(set(ids)) == 1 and ids[0] is not None
            fleet_episode = ids[0]
            assert await admin.fetchval(
                """
                SELECT bool_and(proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[])
                FROM pg_proc
                WHERE oid IN (
                    'public.append_runtime_attention_condition(uuid)'::regprocedure,
                    'public.observe_runtime_attention_conditions()'::regprocedure
                )
                """
            )

            assert await _call_as_role(pool, _SWITCHBOARD, _APPEND_CONDITION, fleet) == (
                fleet_episode
            )

            # Inside its attention grace, and once resolved, nothing is due.
            assert await _call_as_role(pool, _SWITCHBOARD, _APPEND_CONDITION, qa) is None
            assert await _call_as_role(pool, _SWITCHBOARD, _APPEND_CONDITION, resolved) is None
            await admin.execute(
                "UPDATE public.infra_conditions "
                "SET first_detected_at = now() - interval '6 minutes', state = 'aging' "
                "WHERE id = $1",
                qa,
            )
            qa_episode = await _call_as_role(pool, _SWITCHBOARD, _APPEND_CONDITION, qa)
            assert qa_episode is not None and qa_episode != fleet_episode

            # Only the fixed fleet and overdue-QA identities page.
            for not_paging in (stopped, unrelated):
                with pytest.raises(asyncpg.InvalidParameterValueError):
                    await _call_as_role(pool, _SWITCHBOARD, _APPEND_CONDITION, not_paging)
            with pytest.raises(asyncpg.NoDataFoundError):
                await _call_as_role(pool, _SWITCHBOARD, _APPEND_CONDITION, uuid.uuid4())

            rows = {
                row["id"]: row
                for row in await admin.fetch(
                    "SELECT id, lifecycle_state, source_snapshot, payload "
                    "FROM public.runtime_attention_outbox WHERE source = 'control_plane_condition'"
                )
            }
            assert set(rows) == {fleet_episode, qa_episode}
            assert rows[fleet_episode]["lifecycle_state"] == "pending"
            assert rows[fleet_episode]["payload"] == {
                "classification": "fleet_control_unhealthy",
                "door": "/system",
            }
            assert rows[qa_episode]["payload"] == {
                "classification": "qa_patrol_overdue",
                "door": "/system",
            }
            snapshot = rows[fleet_episode]["source_snapshot"]
            assert set(snapshot) == {"condition_id", "condition_kind", "first_detected_at"}
            assert snapshot["condition_id"] == str(fleet)
            assert snapshot["condition_kind"] == "fleet_control"
            stored = json.dumps([dict(row) for row in rows.values()], default=str)
            for leaked in ("general", "127.0.0.1", "refused", "affected"):
                assert leaked not in stored

            # Only Switchboard's active role may produce; no other principal.
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await pool.fetchval(_APPEND_CONDITION, fleet)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await _call_as_role(pool, _MODEL_PRODUCER, _APPEND_CONDITION, fleet)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await _call_as_role(
                    pool,
                    _SWITCHBOARD,
                    "SELECT count(*) FROM public.runtime_attention_condition_episodes",
                )

            # Even bootstrap cannot forge a second episode or an unsafe payload.
            forged = {
                "condition_id": str(fleet),
                "condition_kind": "fleet_control",
                "first_detected_at": "2026-09-29T00:00:00+00:00",
            }
            with pytest.raises(asyncpg.UniqueViolationError):
                await admin.execute(
                    "INSERT INTO public.runtime_attention_outbox (source, source_snapshot, payload) "
                    "VALUES ('control_plane_condition', $1, $2)",
                    forged,
                    {"classification": "fleet_control_unhealthy", "door": "/system"},
                )
            for snapshot_extra, payload in (
                ({}, {"classification": "fleet_control_unhealthy", "door": "/elsewhere"}),
                ({}, {"classification": "general is down", "door": "/system"}),
                (
                    {"summary": "general"},
                    {"classification": "qa_patrol_overdue", "door": "/system"},
                ),
                (
                    {"condition_kind": "qa_patrol_stopped"},
                    {"classification": "qa_patrol_overdue", "door": "/system"},
                ),
            ):
                with pytest.raises(asyncpg.CheckViolationError):
                    await admin.execute(
                        "INSERT INTO public.runtime_attention_outbox (source, source_snapshot, payload) "
                        "VALUES ('control_plane_condition', $1, $2)",
                        {**forged, "condition_id": str(uuid.uuid4()), **snapshot_extra},
                        payload,
                    )

            # Retention: the outbox row ages out; the active condition keeps its
            # episode identity and last delivery category and never re-pages.
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await pool.execute(
                    "DELETE FROM public.runtime_attention_outbox WHERE id = $1", fleet_episode
                )
            for actor in (_SWITCHBOARD, _MODEL_PRODUCER, _DASHBOARD, _CONNECTOR):
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await _call_as_role(
                        pool,
                        actor,
                        "DELETE FROM public.runtime_attention_outbox WHERE id = $1 RETURNING id",
                        fleet_episode,
                    )
            assert (
                await admin.execute(
                    "DELETE FROM public.runtime_attention_outbox WHERE id = $1", fleet_episode
                )
                == "DELETE 1"
            )
            # A separate physical administrative connection independently proves
            # committed retention and the durable marker before any re-append.
            readback = await asyncpg.connect(
                _asyncpg_url(bootstrap_url), timeout=10, command_timeout=30
            )
            try:
                assert await readback.fetchval(
                    "SELECT NOT EXISTS (SELECT FROM public.runtime_attention_outbox WHERE id=$1)",
                    fleet_episode,
                )
                assert await readback.fetchval(
                    "SELECT EXISTS (SELECT FROM public.runtime_attention_outbox WHERE id=$1)",
                    qa_episode,
                )
                assert await readback.fetchval(
                    "SELECT episode_id=$1 FROM public.runtime_attention_condition_episodes "
                    "WHERE condition_id=$2",
                    fleet_episode,
                    fleet,
                )
            finally:
                await readback.close(timeout=10)
            assert await _call_as_role(pool, _SWITCHBOARD, _APPEND_CONDITION, fleet) == (
                fleet_episode
            )
            assert await admin.fetchval(_CONDITION_OUTBOX_COUNT) == 1
            observed = {
                row["condition_id"]: row
                for row in await _fetch_as_role(
                    pool, _DASHBOARD, "SELECT * FROM public.observe_runtime_attention_conditions()"
                )
            }
            assert observed[fleet]["episode_id"] == fleet_episode
            assert observed[fleet]["outbox_retained"] is False
            assert observed[fleet]["lifecycle_state"] == "pending"
            assert observed[qa]["outbox_retained"] is True
            assert observed[qa]["condition_kind"] == "qa_patrol_overdue"
            # The fixed dashboard capability owns observation. The actual normal
            # login and delivery-only Switchboard cannot acquire that authority.
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await pool.fetch("SELECT * FROM public.observe_runtime_attention_conditions()")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await _call_as_role(
                    pool,
                    _SWITCHBOARD,
                    "SELECT count(*) FROM public.observe_runtime_attention_conditions()",
                )
        finally:
            _ROLE_CONTROL_URLS.pop(pool, None)
            try:
                await _close_pool(pool)
            finally:
                await _close_pool(admin)

    asyncio.run(scenario())


def test_condition_attention_survives_bootstrap_replay_and_rollback_keeps_evidence(
    postgres_container,
) -> None:
    """REQ-butler-control-plane-liveness-007 AC3: retained evidence and fencing.

    REQ-database-security-017/025 replaces arbitrary policy auto-repair with
    pre-effect refusal, complete immutable readback, and exact fixture-poison
    inverse before current finalization. All original forced-RLS, single-policy,
    role, retained-evidence, marker backfill and re-enable/dedup positives execute.
    """
    from butlers.core.fleet_conditions import (
        FLEET_FINGERPRINT,
        FLEET_SOURCE,
        QA_PATROL_OVERDUE_FINGERPRINT,
        QA_PATROL_SOURCE,
    )

    db_url, bootstrap_url = _fresh_condition_database(postgres_container)
    migration_role = unquote(urlparse(db_url).username or "")

    async def append(pool: asyncpg.Pool, condition_id: uuid.UUID) -> object:
        return await _call_as_role(pool, _SWITCHBOARD, _APPEND_CONDITION, condition_id)

    async def open_pools() -> tuple[asyncpg.Pool, asyncpg.Pool]:
        admin = await asyncpg.create_pool(_asyncpg_url(bootstrap_url), min_size=1, max_size=2)
        try:
            pool = await asyncpg.create_pool(_asyncpg_url(db_url), min_size=1, max_size=2)
        except BaseException:
            await _close_pool(admin)
            raise
        _ROLE_CONTROL_URLS[pool] = _asyncpg_url(bootstrap_url)
        return admin, pool

    async def before_replay() -> tuple[uuid.UUID, uuid.UUID]:
        admin, pool = await open_pools()
        try:
            fleet = await _insert_condition(admin, FLEET_SOURCE, FLEET_FINGERPRINT)
            episode = await append(admin, fleet)
            assert episode is not None
            # Named adversarial poison only: no fabricated object grant.
            await admin.execute(
                "CREATE POLICY stray_open ON public.runtime_attention_condition_episodes "
                "FOR ALL TO PUBLIC USING (true)"
            )
            return fleet, episode
        finally:
            _ROLE_CONTROL_URLS.pop(pool, None)
            try:
                await _close_pool(pool)
            finally:
                await _close_pool(admin)

    fleet, episode = asyncio.run(before_replay())
    poisoned = _attention_snapshot(db_url)
    with pytest.raises(AuthorityError):
        _rerun_actual_init_db(bootstrap_url, db_url)
    assert _attention_snapshot(db_url) == poisoned
    # REQ-database-security-017/025: exact inverse of this named fixture poison,
    # then actual current convergence. No broad policy reset or refusal-only case.
    repair = create_engine(bootstrap_url)
    try:
        with repair.begin() as conn:
            conn.execute(
                text("DROP POLICY stray_open ON public.runtime_attention_condition_episodes")
            )
    finally:
        repair.dispose()
    _rerun_actual_init_db(bootstrap_url, db_url)
    assert _has_exact_finalized_runtime_attention_interface(db_url)

    async def after_replay() -> None:
        admin, pool = await open_pools()
        try:
            shape = await admin.fetchrow(
                f"""
                SELECT
                    marker.relrowsecurity AND marker.relforcerowsecurity AS forced_rls,
                    pg_get_userbyid(marker.relowner) AS marker_owner,
                    ARRAY(
                        SELECT polname::text FROM pg_policy WHERE polrelid = marker.oid
                    ) AS policies,
                    has_table_privilege('{_MODEL_PRODUCER}', marker.oid, 'SELECT') AS producer_reads,
                    has_function_privilege(
                        '{_MODEL_PRODUCER}',
                        'public.append_runtime_attention_condition(uuid)'::regprocedure,
                        'EXECUTE'
                    ) AS producer_appends,
                    has_function_privilege(
                        '{_SWITCHBOARD}',
                        'public.append_runtime_attention_condition(uuid)'::regprocedure,
                        'EXECUTE'
                    ) AS switchboard_appends,
                    to_regprocedure('public.runtime_attention_upgrade_condition_v4()') IS NOT NULL AS migration_upgrades,
                    has_function_privilege(
                        $1::name,
                        'public.observe_runtime_attention_conditions()'::regprocedure,
                        'EXECUTE'
                    ) AS migration_observes,
                    has_function_privilege(
                        'dashboard_api',
                        'public.observe_runtime_attention_conditions()'::regprocedure,
                        'EXECUTE'
                    ) AS dashboard_observes,
                    pg_get_userbyid(upgrader.proowner) AS upgrader_owner
                FROM pg_class AS marker
                JOIN pg_proc AS upgrader
                  ON upgrader.oid = 'public.append_runtime_attention_condition(uuid)'::regprocedure
                WHERE marker.oid = 'public.runtime_attention_condition_episodes'::regclass
                """,
                migration_role,
            )
            assert dict(shape) == {
                "forced_rls": True,
                "marker_owner": "runtime_attention_outbox_owner",
                "policies": ["runtime_attention_condition_episodes_owner"],
                "producer_reads": False,
                "producer_appends": False,
                "switchboard_appends": True,
                "migration_upgrades": False,
                "migration_observes": False,
                "dashboard_observes": True,
                "upgrader_owner": "runtime_attention_outbox_owner",
            }
            assert await append(admin, fleet) == episode

            # bu-giazn6: the v5 marker sync survives the replay re-fenced.
            marker_sync = await admin.fetchrow(
                f"""
                SELECT
                    pg_get_userbyid(sync.proowner) AS sync_owner,
                    sync.prosecdef AS sync_definer,
                    sync.proconfig AS sync_config,
                    has_function_privilege('public', sync.oid, 'EXECUTE') AS public_executes,
                    has_function_privilege($1::name, sync.oid, 'EXECUTE') AS migration_executes,
                    has_function_privilege('{_SWITCHBOARD}', sync.oid, 'EXECUTE')
                        AS switchboard_executes,
                    to_regprocedure('public.runtime_attention_upgrade_condition_marker_v5()') IS NOT NULL AS migration_upgrades,
                    ARRAY(
                        SELECT trigger_row.tgname::text
                        FROM pg_trigger AS trigger_row
                        WHERE trigger_row.tgrelid = 'public.runtime_attention_outbox'::regclass
                          AND trigger_row.tgfoid = sync.oid
                          AND NOT trigger_row.tgisinternal
                    ) AS sync_triggers
                FROM pg_proc AS sync
                WHERE sync.oid = 'public.runtime_attention_sync_condition_marker()'::regprocedure
                """,
                migration_role,
            )
            assert dict(marker_sync) == {
                "sync_owner": "runtime_attention_outbox_owner",
                "sync_definer": True,
                "sync_config": ["search_path=pg_catalog, pg_temp"],
                "public_executes": False,
                "migration_executes": False,
                "switchboard_executes": False,
                "migration_upgrades": False,
                "sync_triggers": ["runtime_attention_condition_marker_sync_trigger"],
            }

            # Stop the genuine normal-login session before the actual selected
            # maintenance operator proves quiescence. All later producer probes
            # use explicit administrative SET ROLE on separate acquisitions.
            _ROLE_CONTROL_URLS.pop(pool, None)
            await _close_pool(pool)
            with pytest.raises(AuthorityError):
                _attention_transition(db_url, "marker", False, maintenance=False)
            _attention_transition(db_url, "marker", False)
            assert (
                await admin.fetchval(
                    "SELECT to_regprocedure('public.runtime_attention_sync_condition_marker()')"
                )
                is None
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM pg_trigger "
                    "WHERE tgname = 'runtime_attention_condition_marker_sync_trigger'"
                )
                == 0
            )
            assert (
                await admin.fetchval(
                    "SELECT episode_id FROM public.runtime_attention_condition_episodes "
                    "WHERE condition_id = $1",
                    fleet,
                )
                == episode
            )
            # A marker left stale while the trigger was absent is backfilled by
            # the actual held maintenance transition, not a serving capability.
            await admin.execute(
                "UPDATE public.runtime_attention_condition_episodes "
                "SET last_delivery_state = 'failed' WHERE condition_id = $1",
                fleet,
            )
            _attention_transition(db_url, "marker", True)
            assert (
                await admin.fetchval(
                    "SELECT last_delivery_state FROM public.runtime_attention_condition_episodes "
                    "WHERE condition_id = $1",
                    fleet,
                )
                == "pending"
            )
            assert await admin.fetchval(_CONDITION_OUTBOX_COUNT) == 1
            with pytest.raises(AuthorityError):
                _attention_transition(db_url, "marker", True, maintenance=False)

            # Rollback: no new appends, every emitted row and marker kept.
            _attention_transition(db_url, "conditions", False)
            with pytest.raises(AuthorityError):
                _attention_transition(db_url, "conditions", False, maintenance=False)
            qa = await _insert_condition(admin, QA_PATROL_SOURCE, QA_PATROL_OVERDUE_FINGERPRINT)
            assert await append(admin, qa) is None
            assert await append(admin, fleet) is None
            assert await admin.fetchval(_CONDITION_OUTBOX_COUNT) == 1
            assert (
                await admin.fetchval(
                    "SELECT episode_id FROM public.runtime_attention_condition_episodes "
                    "WHERE condition_id = $1",
                    fleet,
                )
                == episode
            )

            # Re-enable: the due QA condition pages once; the fleet one does not re-page.
            _attention_transition(db_url, "conditions", True)
            assert await append(admin, fleet) == episode
            assert await append(admin, qa) is not None
            assert await admin.fetchval(_CONDITION_OUTBOX_COUNT) == 2
        finally:
            _ROLE_CONTROL_URLS.pop(pool, None)
            try:
                await _close_pool(pool)
            finally:
                await _close_pool(admin)

    asyncio.run(after_replay())


# bu-mms5xl: no runtime-attention definer resolves names through public
# ---------------------------------------------------------------------------

_PINNED_SEARCH_PATH = "search_path=pg_catalog, pg_temp"
_RUNTIME_ATTENTION_DEFINERS_SQL = """
    SELECT p.oid::regprocedure::text AS signature, p.proconfig
    FROM pg_proc AS p
    JOIN pg_namespace AS n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public' AND p.prosecdef AND p.proname LIKE '%runtime_attention%'
    ORDER BY 1
"""
# The migration login can CREATE in public; an int4 overload beats the
# catalog's hashtextextended(text, bigint) for a bare ``0`` seed.
_DECOY_SQL = """
    CREATE FUNCTION public.hashtextextended(text, integer) RETURNS bigint
    LANGUAGE plpgsql AS $decoy$
    BEGIN
        RAISE EXCEPTION 'hijacked as %', current_user;
    END;
    $decoy$
"""


def test_runtime_attention_definers_never_run_a_public_decoy(postgres_container) -> None:
    """bu-mms5xl: fleet-halt, model-breaker, and reissue definers ignore public.

    The same database is first regressed to the pre-fix search_path (the shape
    of every database bootstrapped before this change) to prove the decoy is
    live, then converged by the real init-db rerun, which must pin every
    runtime-attention definer idempotently.
    """
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    _upgrade_to_core_head(db_url)

    def definers(conn) -> dict[str, list[str]]:
        return {
            row.signature: list(row.proconfig or [])
            for row in conn.execute(text(_RUNTIME_ATTENTION_DEFINERS_SQL))
        }

    admin = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    migration = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            fresh = definers(conn)
            assert {
                "append_runtime_attention_fleet_halt()",
                "append_runtime_attention_model_breaker(bigint)",
                "reissue_runtime_attention_episode(uuid)",
                "append_runtime_attention_condition(uuid)",
                "observe_runtime_attention_conditions()",
                "runtime_attention_sync_condition_marker()",
            } <= set(fresh)
            assert all(config == [_PINNED_SEARCH_PATH] for config in fresh.values()), fresh

        entry_id, trigger_id = _seed_legacy_breaker_edge(db_url)
        uncertain_id = uuid.uuid4()
        with admin.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO public.model_dispatch_attempts "
                    "(catalog_entry_id, butler, outcome, failure_reason) VALUES "
                    "(:entry, 'general', 'quota_skip', 'Monthly spend ceiling reached: test')"
                ),
                {"entry": entry_id},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO public.runtime_attention_outbox (
                        id, source, lifecycle_state, triggering_attempt_id, source_snapshot,
                        payload, claim_token, claim_epoch, delivery_lease_epoch,
                        claimed_by_instance, claimed_at, claim_expires_at,
                        delivery_error_class, delivery_error_detail
                    ) VALUES (
                        :id, 'model_breaker', 'uncertain', -1,
                        jsonb_build_object(
                            'catalog_entry_id', CAST(:entry AS text), 'alias', 'decoy',
                            'model_id', 'decoy', 'triggering_attempt_id', -1,
                            'consecutive_failures', 5
                        ),
                        jsonb_build_object(
                            'classification', 'model_breaker_open',
                            'consecutive_failures', 5, 'door', '/settings/models'
                        ),
                        gen_random_uuid(), 1, 1, 'dead-worker', now() - interval '2 minutes',
                        now() - interval '1 minute', 'transport_uncertain', 'worker_recovery'
                    )
                    """
                ),
                {"id": uncertain_id, "entry": str(entry_id)},
            )
            # Regress to the pre-fix path an older bootstrap left behind.
            for signature in fresh:
                conn.execute(
                    text(
                        f"ALTER FUNCTION public.{signature} "
                        "SET search_path = pg_catalog, public, pg_temp"
                    )
                )
        with migration.connect() as conn:
            conn.execute(text(_DECOY_SQL))

        calls = {
            "model_breaker": (
                "butler_general_rw",
                "SELECT public.append_runtime_attention_model_breaker(:trigger)",
            ),
            "fleet_halt": (
                "butler_general_rw",
                "SELECT public.append_runtime_attention_fleet_halt()",
            ),
            "reissue": (
                _DASHBOARD,
                "SELECT successor_episode_id "
                "FROM public.reissue_runtime_attention_episode(:uncertain)",
            ),
        }

        def call(name: str):
            role, statement = calls[name]
            with (admin if role is not None else migration).connect() as conn:
                if role is not None:
                    conn.execute(text(f"SET ROLE {_quote_ident(role)}"))
                try:
                    return conn.execute(
                        text(statement), {"trigger": trigger_id, "uncertain": uncertain_id}
                    ).scalar_one()
                finally:
                    if role is not None:
                        conn.execute(text("RESET ROLE"))

        # The decoy is live against the regressed path: this is the hole.  The
        # fleet-halt body is re-adopted on every rerun and already casts its
        # seed to bigint, so only the path guards the two one-shot bodies.
        for name in ("model_breaker", "reissue"):
            with pytest.raises(DBAPIError, match="hijacked as runtime_attention_outbox_owner"):
                call(name)

        # Unknown source-unlisted overloads refuse before convergence. The
        # planted decoy stays unchanged; remove only this test-owned poison,
        # then the actual source finalizer repairs the regressed safe functions.
        poisoned = _attention_snapshot(db_url)
        with pytest.raises(AuthorityError):
            _rerun_actual_init_db(bootstrap_url, db_url)
        assert _attention_snapshot(db_url) == poisoned
        with admin.connect() as conn:
            assert conn.execute(
                text("SELECT to_regprocedure('public.hashtextextended(text,integer)') IS NOT NULL")
            ).scalar_one()
            conn.execute(text("DROP FUNCTION public.hashtextextended(text,integer)"))
        _rerun_actual_init_db(bootstrap_url, db_url)
        _rerun_actual_init_db(bootstrap_url, db_url)
        with admin.connect() as conn:
            assert definers(conn) == fresh
        assert _has_exact_finalized_runtime_attention_interface(db_url)

        for name in calls:
            assert call(name) is not None, name
    finally:
        migration.dispose()
        admin.dispose()
