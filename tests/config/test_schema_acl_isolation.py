"""Integration tests for one-DB schema ACL isolation and intentional fanout reads.

Issue: butlers-1003.6
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from butlers.testing.migration import bootstrap_extensions

# Skip all tests if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_BUTLER_SCHEMAS = ("general", "health", "messenger", "relationship", "switchboard")
_RUNTIME_ROLES = {
    "general": "butler_general_rw",
    "health": "butler_health_rw",
    "messenger": "butler_messenger_rw",
    "relationship": "butler_relationship_rw",
    "switchboard": "butler_switchboard_rw",
}


def _quote_ident(identifier: str) -> str:
    """Quote an identifier for SQL text construction."""
    return '"' + identifier.replace('"', '""') + '"'


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def postgres_container():
    """Start a PostgreSQL container for migration tests."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg17") as postgres:
        yield postgres


def _create_db(postgres_container, db_name: str) -> str:
    """Create a fresh database and return its SQLAlchemy URL."""
    admin_url = postgres_container.get_connection_url()
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        safe = db_name.replace('"', '""')
        conn.execute(text(f'CREATE DATABASE "{safe}"'))
    engine.dispose()

    host = postgres_container.get_container_host_ip()
    port = postgres_container.get_exposed_port(5432)
    user = postgres_container.username
    password = postgres_container.password
    return f"postgresql://{user}:{password}@{host}:{port}/{db_name}"


def _role_exists(db_url: str, role_name: str) -> bool:
    """Return True when role exists in pg_roles."""
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :r)"),
            {"r": role_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def _require_runtime_acl(db_url: str) -> None:
    """Skip tests when runtime ACL migration has not yet been applied."""
    missing = [role for role in _RUNTIME_ROLES.values() if not _role_exists(db_url, role)]
    if missing:
        pytest.skip(
            "Runtime ACL roles are not present; requires core runtime ACL migration "
            f"(missing: {', '.join(missing)})"
        )


def _execute_as_role(db_url: str, role_name: str, sql: str, *, scalar: bool = False):
    """Execute SQL after SET ROLE and optionally return scalar result."""
    quoted_role = _quote_ident(role_name)
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET ROLE {quoted_role}"))
            try:
                result = conn.execute(text(sql))
                if scalar:
                    return result.scalar()
                return None
            finally:
                conn.execute(text("RESET ROLE"))
    finally:
        engine.dispose()


def test_runtime_roles_are_limited_to_own_schema_and_shared(postgres_container):
    """Each runtime role can write own schema, read shared, and cannot read another schema."""
    from butlers.migrations import run_migrations

    db_url = _create_db(postgres_container, _unique_db_name())
    bootstrap_extensions(db_url)
    asyncio.run(run_migrations(db_url, chain="core"))
    _require_runtime_acl(db_url)

    setup_engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with setup_engine.connect() as conn:
            for schema in _BUTLER_SCHEMAS:
                conn.execute(
                    text(f"CREATE TABLE {schema}.acl_probe (id INT PRIMARY KEY, note TEXT)")
                )
            conn.execute(
                text("CREATE TABLE public.acl_probe_shared (id INT PRIMARY KEY, note TEXT)")
            )
            conn.execute(
                text("INSERT INTO public.acl_probe_shared (id, note) VALUES (1, 'shared-ok')")
            )
    finally:
        setup_engine.dispose()

    for owned_schema, runtime_role in _RUNTIME_ROLES.items():
        _execute_as_role(
            db_url,
            runtime_role,
            f"INSERT INTO {owned_schema}.acl_probe (id, note) VALUES (1, '{owned_schema}-ok')",
        )
        own_note = _execute_as_role(
            db_url,
            runtime_role,
            f"SELECT note FROM {owned_schema}.acl_probe WHERE id = 1",
            scalar=True,
        )
        assert own_note == f"{owned_schema}-ok"

        shared_note = _execute_as_role(
            db_url,
            runtime_role,
            "SELECT note FROM public.acl_probe_shared WHERE id = 1",
            scalar=True,
        )
        assert shared_note == "shared-ok"

        with pytest.raises(ProgrammingError, match="permission denied"):
            _execute_as_role(
                db_url,
                runtime_role,
                "INSERT INTO public.acl_probe_shared (id, note) VALUES (2, 'blocked')",
            )

        blocked_schema = next(schema for schema in _BUTLER_SCHEMAS if schema != owned_schema)
        with pytest.raises(ProgrammingError, match="permission denied"):
            _execute_as_role(
                db_url,
                runtime_role,
                f"SELECT id FROM {blocked_schema}.acl_probe LIMIT 1",
            )


def test_privileged_cross_schema_aggregate_reads_are_allowed(postgres_container):
    """Privileged connections can aggregate across butler schemas intentionally."""
    from butlers.migrations import run_migrations

    db_url = _create_db(postgres_container, _unique_db_name())
    bootstrap_extensions(db_url)
    asyncio.run(run_migrations(db_url, chain="core"))
    _require_runtime_acl(db_url)

    setup_engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with setup_engine.connect() as conn:
            conn.execute(text("CREATE TABLE general.acl_fanout (id INT PRIMARY KEY)"))
            conn.execute(text("CREATE TABLE health.acl_fanout (id INT PRIMARY KEY)"))
            conn.execute(text("INSERT INTO general.acl_fanout (id) VALUES (1), (2)"))
            conn.execute(text("INSERT INTO health.acl_fanout (id) VALUES (1)"))
    finally:
        setup_engine.dispose()

    admin_engine = create_engine(db_url)
    try:
        with admin_engine.connect() as conn:
            total = conn.execute(
                text(
                    "SELECT SUM(cnt) FROM ("
                    "  SELECT COUNT(*)::INT AS cnt FROM general.acl_fanout "
                    "  UNION ALL "
                    "  SELECT COUNT(*)::INT AS cnt FROM health.acl_fanout"
                    ") t"
                )
            ).scalar()
    finally:
        admin_engine.dispose()

    assert total == 3

    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role(
            db_url,
            _RUNTIME_ROLES["general"],
            "SELECT SUM(cnt) FROM ("
            "  SELECT COUNT(*)::INT AS cnt FROM general.acl_fanout "
            "  UNION ALL "
            "  SELECT COUNT(*)::INT AS cnt FROM health.acl_fanout"
            ") t",
            scalar=True,
        )


# ---------------------------------------------------------------------------
# SET ROLE enforcement integration tests (task 7 of schema-isolation-enforcement)
# ---------------------------------------------------------------------------

# Subset of public tables guaranteed to exist after a full core migration run.
# Ordered to minimise FK concerns; each row uses only NOT-NULL columns.
_PUBLIC_WRITE_MATRIX_INSERTS: list[tuple[str, str]] = [
    # (table_name, minimal_INSERT_statement)
    (
        "entities",
        "INSERT INTO public.entities (canonical_name, entity_type)"
        " VALUES ('acl-probe-entity', 'other')",
    ),
    (
        "contacts",
        "INSERT INTO public.contacts (name) VALUES ('acl-probe-contact')",
    ),
    (
        "contact_info",
        "INSERT INTO public.contact_info (contact_id, type, value)"
        " SELECT id, 'email', 'acl-probe@example.com' FROM public.contacts"
        " WHERE name = 'acl-probe-contact' LIMIT 1",
    ),
    (
        "entity_info",
        "INSERT INTO public.entity_info (entity_id, key, value)"
        " SELECT id, 'acl-probe-key', 'acl-probe-val' FROM public.entities"
        " WHERE canonical_name = 'acl-probe-entity' LIMIT 1",
    ),
    (
        "google_accounts",
        "INSERT INTO public.google_accounts (google_user_id, email)"
        " VALUES ('acl-probe-gid', 'acl-probe-google@example.com')",
    ),
    (
        "steam_accounts",
        "INSERT INTO public.steam_accounts (steam_id, account_name)"
        " VALUES ('76561198000000001', 'acl-probe-steam')",
    ),
    (
        "user_context",
        "INSERT INTO public.user_context (signal_type, set_by_butler, expires_at)"
        " VALUES ('acl-probe-signal', 'general', now() + interval '1 hour')",
    ),
    (
        "model_round_robin_counters",
        "INSERT INTO public.model_round_robin_counters (butler_name, complexity_tier, counter)"
        " VALUES ('acl-probe-butler', 'medium', 0)"
        " ON CONFLICT (butler_name, complexity_tier) DO NOTHING",
    ),
    (
        "token_usage_ledger",
        "INSERT INTO public.token_usage_ledger (session_id, butler_name, model_alias,"
        " input_tokens, output_tokens, total_tokens)"
        " VALUES (gen_random_uuid(), 'general', 'acl-probe', 10, 10, 20)",
    ),
    (
        "ingestion_events",
        "INSERT INTO public.ingestion_events"
        " (id, source_channel, source_provider, source_endpoint_identity,"
        "  external_event_id, dedupe_key, dedupe_strategy, ingestion_tier, policy_tier)"
        " VALUES (gen_random_uuid(), 'acl', 'probe', 'ep-1',"
        "  'ext-1', 'dk-acl-probe-1', 'hash', 'full', 'standard')",
    ),
    (
        "healing_attempts",
        "INSERT INTO public.healing_attempts"
        " (fingerprint, butler_name, severity, exception_type, call_site)"
        " VALUES ('acl-probe-fp', 'general', 3, 'AclProbeError', 'probe.py:1')",
    ),
    (
        "qa_dismissals",
        "INSERT INTO public.qa_dismissals (fingerprint, dismissed_by, reason)"
        " VALUES ('acl-probe-fp', 'general', 'acl probe test')",
    ),
    (
        "qa_findings",
        "INSERT INTO public.qa_findings"
        " (butler_name, fingerprint, severity, category, message)"
        " VALUES ('general', 'acl-probe-fp', 'warning', 'acl', 'probe')",
    ),
    (
        "qa_repo_config",
        # qa_repo_config is only UPDATE-granted (not INSERT); the row is pre-seeded
        # by test_set_role_allows_public_table_writes via admin before the role loop.
        "UPDATE public.qa_repo_config SET enabled = true WHERE repo_path = '/acl-probe-repo'",
    ),
    (
        "qa_patrols",
        "INSERT INTO public.qa_patrols (butler_name, status) VALUES ('general', 'running')",
    ),
    (
        "memory_catalog",
        "INSERT INTO public.memory_catalog"
        " (source_schema, source_table, source_id)"
        " VALUES ('general', 'acl_probe', gen_random_uuid())",
    ),
    (
        "insight_candidates",
        "INSERT INTO public.insight_candidates"
        " (origin_butler, priority, category, dedup_key, expires_at, message)"
        " VALUES ('general', 1, 'acl', 'acl-probe-dk', now() + interval '1 day', 'probe')",
    ),
    (
        "insight_cooldowns",
        "INSERT INTO public.insight_cooldowns (dedup_key, expires_at)"
        " VALUES ('acl-cooldown-probe', now() + interval '1 day')"
        " ON CONFLICT (dedup_key) DO NOTHING",
    ),
    (
        "insight_engagement",
        "INSERT INTO public.insight_engagement"
        " (insight_id, contact_id, action)"
        " VALUES (gen_random_uuid(), gen_random_uuid(), 'viewed')",
    ),
    (
        "insight_settings",
        "INSERT INTO public.insight_settings (contact_id, channel, enabled)"
        " VALUES (gen_random_uuid(), 'acl-probe', true)"
        " ON CONFLICT (contact_id, channel) DO NOTHING",
    ),
]


def _connector_role_exists(db_url: str) -> bool:
    """Return True when the connector_writer role exists."""
    return _role_exists(db_url, "connector_writer")


def _require_connector_writer(db_url: str) -> None:
    """Skip tests when connector_writer role has not been created."""
    if not _connector_role_exists(db_url):
        pytest.skip("connector_writer role is not present; requires core runtime ACL migration")


def test_set_role_enforces_own_schema_write(postgres_container):
    """SET ROLE butler_general_rw: INSERT into general.state succeeds."""
    from butlers.migrations import run_migrations

    db_url = _create_db(postgres_container, _unique_db_name())
    bootstrap_extensions(db_url)
    asyncio.run(run_migrations(db_url, chain="core"))
    _require_runtime_acl(db_url)

    # state table lives in every butler schema; created by core_001_foundation.
    _execute_as_role(
        db_url,
        _RUNTIME_ROLES["general"],
        "INSERT INTO general.state (key, value) VALUES ('acl-probe-own', '\"ok\"'::jsonb)",
    )

    note = _execute_as_role(
        db_url,
        _RUNTIME_ROLES["general"],
        "SELECT value FROM general.state WHERE key = 'acl-probe-own'",
        scalar=True,
    )
    assert note == '"ok"'


def test_set_role_blocks_cross_schema_write(postgres_container):
    """SET ROLE butler_general_rw: INSERT into health.state raises permission denied."""
    from butlers.migrations import run_migrations

    db_url = _create_db(postgres_container, _unique_db_name())
    bootstrap_extensions(db_url)
    asyncio.run(run_migrations(db_url, chain="core"))
    _require_runtime_acl(db_url)

    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role(
            db_url,
            _RUNTIME_ROLES["general"],
            "INSERT INTO health.state (key, value)"
            " VALUES ('acl-probe-cross', '\"blocked\"'::jsonb)",
        )


def test_set_role_allows_public_table_writes(postgres_container):
    """SET ROLE butler_general_rw: write succeeds for each table in the public write matrix.

    Iterates all 20 public tables guaranteed to exist after the core migration chain.
    Each test row is written under the runtime role; success proves the write grant
    is in effect.

    Note: qa_repo_config is only UPDATE-granted (not INSERT), so a seed row is
    pre-inserted via the admin connection before the role loop runs.
    """
    from butlers.migrations import run_migrations

    db_url = _create_db(postgres_container, _unique_db_name())
    bootstrap_extensions(db_url)
    asyncio.run(run_migrations(db_url, chain="core"))
    _require_runtime_acl(db_url)

    # Pre-seed the qa_repo_config row that the role will UPDATE (no INSERT grant).
    seed_engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with seed_engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO public.qa_repo_config (repo_path, enabled)"
                    " VALUES ('/acl-probe-repo', false)"
                    " ON CONFLICT (repo_path) DO NOTHING"
                )
            )
    finally:
        seed_engine.dispose()

    role = _RUNTIME_ROLES["general"]
    failed: list[tuple[str, str]] = []

    for table_name, insert_sql in _PUBLIC_WRITE_MATRIX_INSERTS:
        try:
            _execute_as_role(db_url, role, insert_sql)
        except Exception as exc:
            failed.append((table_name, str(exc)))

    if failed:
        lines = "\n".join(f"  {t}: {e}" for t, e in failed)
        pytest.fail(f"SET ROLE {role!r} write failed for {len(failed)} public tables:\n{lines}")


def test_set_role_blocks_public_table_not_in_matrix(postgres_container):
    """SET ROLE butler_general_rw: INSERT into public.model_catalog raises permission denied.

    model_catalog is a read-only table for butler runtime roles; it is managed
    exclusively by migrations and the dashboard.
    """
    from butlers.migrations import run_migrations

    db_url = _create_db(postgres_container, _unique_db_name())
    bootstrap_extensions(db_url)
    asyncio.run(run_migrations(db_url, chain="core"))
    _require_runtime_acl(db_url)

    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role(
            db_url,
            _RUNTIME_ROLES["general"],
            "INSERT INTO public.model_catalog (alias, runtime_type, model_id)"
            " VALUES ('acl-probe', 'sdk', 'claude-probe')",
        )


def test_connector_writer_role_enforcement(postgres_container):
    """SET ROLE connector_writer: can write connectors schema and public.ingestion_events.

    Verifies that connector_writer cannot write to a butler runtime schema.
    """
    from butlers.migrations import run_migrations

    db_url = _create_db(postgres_container, _unique_db_name())
    bootstrap_extensions(db_url)
    asyncio.run(run_migrations(db_url, chain="core"))
    _require_runtime_acl(db_url)
    _require_connector_writer(db_url)

    # connector_writer can INSERT into connectors schema tables.
    # connectors.filtered_events is created by core_007; we need to ensure
    # the partition exists.  Use the partition-ensuring function if available,
    # or insert a probe into the table directly.
    _execute_as_role(
        db_url,
        "connector_writer",
        "SELECT connectors.connectors_filtered_events_ensure_partition(now())",
    )
    _execute_as_role(
        db_url,
        "connector_writer",
        "INSERT INTO connectors.filtered_events"
        " (connector_type, endpoint_identity, event_payload, received_at,"
        "  status, dedupe_key)"
        " VALUES ('probe', 'ep-connector', '{}'::jsonb, now(),"
        "  'accepted', 'acl-connector-dk-1')"
        " ON CONFLICT DO NOTHING",
    )

    # connector_writer can INSERT into public.ingestion_events (in the write matrix).
    _execute_as_role(
        db_url,
        "connector_writer",
        "INSERT INTO public.ingestion_events"
        " (id, source_channel, source_provider, source_endpoint_identity,"
        "  external_event_id, dedupe_key, dedupe_strategy, ingestion_tier, policy_tier)"
        " VALUES (gen_random_uuid(), 'connector-acl', 'probe', 'ep-2',"
        "  'ext-2', 'dk-connector-probe-1', 'hash', 'full', 'standard')",
    )

    # connector_writer cannot INSERT into a butler runtime schema.
    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role(
            db_url,
            "connector_writer",
            "INSERT INTO general.state (key, value)"
            " VALUES ('connector-probe-blocked', '\"blocked\"'::jsonb)",
        )


def test_role_fallback_when_absent(postgres_container, caplog):
    """Database.connect() with a non-existent role creates the pool without SET ROLE.

    The butler should operate normally (shared-user privileges) and log a warning
    rather than raising an exception.
    """
    from butlers.migrations import run_migrations

    db_url = _create_db(postgres_container, _unique_db_name())
    bootstrap_extensions(db_url)
    asyncio.run(run_migrations(db_url, chain="core"))

    from urllib.parse import urlparse

    from butlers.db import Database

    parsed = urlparse(db_url)
    db = Database(
        db_name=parsed.path.lstrip("/"),
        role="butler_nonexistent_role_xyz",
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        user=parsed.username or "postgres",
        password=parsed.password or "postgres",
        min_pool_size=1,
        max_pool_size=2,
    )

    with caplog.at_level(logging.WARNING, logger="butlers.db"):
        pool = asyncio.run(db.connect())

    try:
        # Pool was created; basic queries still work.
        result = asyncio.run(pool.fetchval("SELECT 1"))
        assert result == 1
        # Role verification failed; _role_verified must be False.
        assert not db._role_verified
        # A warning about the missing role should have been logged.
        assert any(
            "butler_nonexistent_role_xyz" in record.message
            for record in caplog.records
            if record.levelno >= logging.WARNING
        ), "Expected a warning about the absent role, got: " + str(
            [r.message for r in caplog.records]
        )
    finally:
        asyncio.run(db.close())


def test_role_reset_on_connection_return(postgres_container):
    """asyncpg RESET ALL + setup callback: role is re-applied on re-acquire.

    Acquires a connection from a SET ROLE pool, verifies the role is active,
    releases the connection back to the pool, then re-acquires and verifies the
    role is re-set by the setup callback (not lost after RESET ALL).
    """
    from butlers.migrations import run_migrations

    db_url = _create_db(postgres_container, _unique_db_name())
    bootstrap_extensions(db_url)
    asyncio.run(run_migrations(db_url, chain="core"))
    _require_runtime_acl(db_url)

    from urllib.parse import urlparse

    from butlers.db import Database

    parsed = urlparse(db_url)
    role = _RUNTIME_ROLES["general"]

    async def _run() -> None:
        db = Database(
            db_name=parsed.path.lstrip("/"),
            role=role,
            host=parsed.hostname or "localhost",
            port=parsed.port or 5432,
            user=parsed.username or "postgres",
            password=parsed.password or "postgres",
            min_pool_size=1,
            max_pool_size=2,
        )
        pool = await db.connect()
        try:
            assert db._role_verified, "Role should have been verified on connect()"

            # First acquire: verify role is set.
            async with pool.acquire() as conn:
                current_role = await conn.fetchval("SELECT current_user")
                assert current_role == role, (
                    f"Expected role {role!r} after acquire, got {current_role!r}"
                )

            # Connection returned to pool; asyncpg runs RESET ALL.
            # Re-acquire: the setup callback should have re-set the role.
            async with pool.acquire() as conn:
                current_role_after_reset = await conn.fetchval("SELECT current_user")
                assert current_role_after_reset == role, (
                    f"Expected role {role!r} after re-acquire (post-RESET ALL), "
                    f"got {current_role_after_reset!r}"
                )
        finally:
            await db.close()

    asyncio.run(_run())
