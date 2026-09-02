"""Integration tests for one-DB schema ACL isolation and intentional fanout reads.

Issue: butlers-1003.6
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from urllib.parse import urlparse

import asyncpg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from butlers.testing.migration import (
    create_migration_db,
    init_db_sql_for_dbapi,
    migration_bootstrap_db_url,
)

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
_RESTORE_DRILL_DENIED_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
    "connector_writer",
)


def _quote_ident(identifier: str) -> str:
    """Quote an identifier for SQL text construction."""
    return '"' + identifier.replace('"', '""') + '"'


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


def _create_db(postgres_container, db_name: str) -> str:
    """Create a core-migrated DB and return its normal migration URL.

    The normal migration login stays NOCREATEDB/NOCREATEROLE and must exercise
    the Database ``SET ROLE`` lifecycle. Tests needing privileged setup or
    rollback derive the disposable control URL explicitly.
    """
    from butlers.migrations import run_migrations

    migration_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(migration_url, chain="core"))
    return migration_url


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


def _restore_drill_ledger_effective_privileges(db_url: str, role_name: str) -> dict[str, bool]:
    """Return protected-schema/table privileges through the normal SET ROLE path."""
    quoted_role = _quote_ident(role_name)
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET ROLE {quoted_role}"))
            try:
                return dict(
                    conn.execute(
                        text(
                            """
                            SELECT
                                has_schema_privilege(
                                    current_user,
                                    'restore_drill_executor',
                                    'USAGE'
                                ) AS schema_usage,
                                has_table_privilege(
                                    current_user,
                                    (
                                        SELECT ledger.oid
                                        FROM pg_catalog.pg_class AS ledger
                                        JOIN pg_catalog.pg_namespace AS result_schema
                                            ON result_schema.oid = ledger.relnamespace
                                        WHERE result_schema.nspname = 'restore_drill_executor'
                                          AND ledger.relname = 'restore_drill_results'
                                          AND ledger.relkind = 'r'
                                    ),
                                    'SELECT'
                                ) AS ledger_select,
                                has_table_privilege(
                                    current_user,
                                    (
                                        SELECT ledger.oid
                                        FROM pg_catalog.pg_class AS ledger
                                        JOIN pg_catalog.pg_namespace AS result_schema
                                            ON result_schema.oid = ledger.relnamespace
                                        WHERE result_schema.nspname = 'restore_drill_executor'
                                          AND ledger.relname = 'restore_drill_results'
                                          AND ledger.relkind = 'r'
                                    ),
                                    'INSERT'
                                ) AS ledger_insert
                            """
                        )
                    )
                    .mappings()
                    .one()
                )
            finally:
                conn.execute(text("RESET ROLE"))
    finally:
        engine.dispose()


def _rerun_init_db_as_control(control_db_url: str, migration_user: str) -> None:
    """Run the trusted bootstrap as the disposable privileged control user."""
    source = init_db_sql_for_dbapi()
    engine = create_engine(control_db_url, isolation_level="AUTOCOMMIT")
    raw_connection = engine.raw_connection()
    try:
        raw_connection.autocommit = True
        with raw_connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('butlers.connecting_user', %s, false)",
                (migration_user,),
            )
            cursor.execute(source)
    finally:
        raw_connection.close()
        engine.dispose()


def _execute_as_role_via_session_auth(
    db_url: str,
    session_role: str,
    role_name: str,
    sql: str,
    *,
    scalar: bool = False,
):
    """Execute SQL via a non-superuser session authorization, then SET ROLE."""
    quoted_role = _quote_ident(role_name)
    quoted_session_role = _quote_ident(session_role)
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET SESSION AUTHORIZATION {quoted_session_role}"))
            conn.execute(text(f"SET ROLE {quoted_role}"))
            try:
                result = conn.execute(text(sql))
                if scalar:
                    return result.scalar()
                return None
            finally:
                conn.execute(text("RESET ROLE"))
                conn.execute(text("RESET SESSION AUTHORIZATION"))
    finally:
        engine.dispose()


def test_runtime_roles_are_limited_to_own_schema_and_shared(postgres_container):
    """Each runtime role can write own schema and public data, but not another schema."""
    db_url = _create_db(postgres_container, _unique_db_name())
    _require_runtime_acl(db_url)

    setup_engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with setup_engine.connect() as conn:
            conn.execute(
                text("CREATE TABLE public.acl_probe_shared (id INT PRIMARY KEY, note TEXT)")
            )
            conn.execute(
                text("INSERT INTO public.acl_probe_shared (id, note) VALUES (1, 'shared-ok')")
            )
            for runtime_role in _RUNTIME_ROLES.values():
                conn.execute(
                    text(
                        f"GRANT SELECT ON TABLE public.acl_probe_shared "
                        f"TO {_quote_ident(runtime_role)}"
                    )
                )
    finally:
        setup_engine.dispose()

    for probe_id, (owned_schema, runtime_role) in enumerate(_RUNTIME_ROLES.items(), start=2):
        _execute_as_role(
            db_url,
            runtime_role,
            f"CREATE TABLE {owned_schema}.acl_probe (id INT PRIMARY KEY, note TEXT)",
        )
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

        _execute_as_role(
            db_url,
            runtime_role,
            f"INSERT INTO public.acl_probe_shared (id, note) VALUES ({probe_id}, 'shared-write-ok')",
        )
        shared_write_note = _execute_as_role(
            db_url,
            runtime_role,
            f"SELECT note FROM public.acl_probe_shared WHERE id = {probe_id}",
            scalar=True,
        )
        assert shared_write_note == "shared-write-ok"

        blocked_schema = next(schema for schema in _BUTLER_SCHEMAS if schema != owned_schema)
        with pytest.raises(ProgrammingError, match="permission denied"):
            _execute_as_role(
                db_url,
                runtime_role,
                f"SELECT id FROM {blocked_schema}.acl_probe LIMIT 1",
            )


def test_privileged_cross_schema_aggregate_reads_are_allowed(postgres_container):
    """Privileged connections can aggregate across butler schemas intentionally."""
    db_url = _create_db(postgres_container, _unique_db_name())
    _require_runtime_acl(db_url)

    _execute_as_role(
        db_url,
        _RUNTIME_ROLES["general"],
        "CREATE TABLE general.acl_fanout (id INT PRIMARY KEY)",
    )
    _execute_as_role(
        db_url,
        _RUNTIME_ROLES["health"],
        "CREATE TABLE health.acl_fanout (id INT PRIMARY KEY)",
    )
    _execute_as_role(
        db_url,
        _RUNTIME_ROLES["general"],
        "INSERT INTO general.acl_fanout (id) VALUES (1), (2)",
    )
    _execute_as_role(
        db_url,
        _RUNTIME_ROLES["health"],
        "INSERT INTO health.acl_fanout (id) VALUES (1)",
    )

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
    # public.contacts was dropped by core_134 (contacts-schema retirement); it is no
    # longer a writable public table, so it is excluded from this write matrix.
    (
        "entity_info",
        "INSERT INTO public.entity_info (entity_id, type, value)"
        " SELECT id, 'acl-probe-key', 'acl-probe-val' FROM public.entities"
        " WHERE canonical_name = 'acl-probe-entity' LIMIT 1",
    ),
    (
        "google_accounts",
        "INSERT INTO public.google_accounts (entity_id, email)"
        " SELECT id, 'acl-probe-google@example.com' FROM public.entities"
        " WHERE canonical_name = 'acl-probe-entity' LIMIT 1",
    ),
    (
        "steam_accounts",
        "INSERT INTO public.steam_accounts (entity_id, steam_id, display_name)"
        " SELECT id, 76561198000000001, 'acl-probe-steam' FROM public.entities"
        " WHERE canonical_name = 'acl-probe-entity' LIMIT 1",
    ),
    (
        "user_context",
        "INSERT INTO public.user_context (signal_type, set_by_butler, expires_at)"
        " VALUES ('acl-probe-signal', 'general', now() + interval '1 hour')",
    ),
    (
        "model_round_robin_counters",
        "INSERT INTO public.model_round_robin_counters (butler_name, complexity_tier, counter)"
        " VALUES ('acl-probe-butler', 'workhorse', 0)"
        " ON CONFLICT (butler_name, complexity_tier) DO NOTHING",
    ),
    (
        "token_usage_ledger",
        "INSERT INTO public.token_usage_ledger"
        " (catalog_entry_id, session_id, butler_name, input_tokens, output_tokens)"
        " SELECT id, gen_random_uuid(), 'general', 10, 10"
        " FROM public.model_catalog"
        " ORDER BY created_at"
        " LIMIT 1",
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
        "INSERT INTO public.qa_dismissals (fingerprint, dismissed_until, dismissed_by)"
        " VALUES ('acl-probe-fp', now() + interval '1 day', 'general')",
    ),
    (
        "qa_findings",
        "WITH patrol AS ("
        "  INSERT INTO public.qa_patrols (status) VALUES ('running') RETURNING id"
        ")"
        " INSERT INTO public.qa_findings"
        " (patrol_id, fingerprint, source_type, source_butler, severity,"
        "  exception_type, event_summary, call_site, first_seen, last_seen)"
        " SELECT id, 'acl-probe-fp', 'log_scanner', 'general', 3,"
        "  'AclProbeError', 'probe', 'probe.py:1', now(), now()"
        " FROM patrol",
    ),
    (
        "qa_repo_config",
        # qa_repo_config is only UPDATE-granted (not INSERT); the row is pre-seeded
        # by test_set_role_allows_public_table_writes via admin before the role loop.
        "UPDATE public.qa_repo_config"
        " SET repo_url = 'https://example.com/acl-probe-updated.git'"
        " WHERE singleton = true",
    ),
    (
        "qa_patrols",
        "INSERT INTO public.qa_patrols (status) VALUES ('running')",
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
        "INSERT INTO public.insight_cooldowns (dedup_key, cooldown_until)"
        " VALUES ('acl-cooldown-probe', now() + interval '1 day')"
        " ON CONFLICT (dedup_key) DO NOTHING",
    ),
    (
        "insight_engagement",
        "INSERT INTO public.insight_engagement"
        " (insight_id, engaged)"
        " SELECT id, true FROM public.insight_candidates"
        " WHERE dedup_key = 'acl-probe-dk' LIMIT 1",
    ),
    (
        "insight_settings",
        "UPDATE public.insight_settings SET verbosity = 'normal' WHERE id = 1",
    ),
    (
        "expected_signals",
        "INSERT INTO public.expected_signals "
        "(signal_key, producer, expected_cadence_seconds, last_observed_at, "
        "measurability, evaluated_at) "
        "VALUES ('general:acl-probe', 'owner', 3600, now(), 'present', now())",
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
    """SET ROLE butler_general_rw: INSERT into an own-schema table succeeds."""
    db_url = _create_db(postgres_container, _unique_db_name())
    _require_runtime_acl(db_url)

    _execute_as_role(
        db_url,
        _RUNTIME_ROLES["general"],
        "CREATE TABLE IF NOT EXISTS general.acl_probe_own_write "
        "(key TEXT PRIMARY KEY, value JSONB NOT NULL)",
    )

    _execute_as_role(
        db_url,
        _RUNTIME_ROLES["general"],
        "INSERT INTO general.acl_probe_own_write (key, value)"
        " VALUES ('acl-probe-own', '\"ok\"'::jsonb)",
    )

    note = _execute_as_role(
        db_url,
        _RUNTIME_ROLES["general"],
        "SELECT value FROM general.acl_probe_own_write WHERE key = 'acl-probe-own'",
        scalar=True,
    )
    assert note == "ok"


def test_set_role_blocks_cross_schema_write(postgres_container):
    """SET ROLE butler_general_rw: INSERT into another schema table is denied."""
    db_url = _create_db(postgres_container, _unique_db_name())
    _require_runtime_acl(db_url)

    _execute_as_role(
        db_url,
        _RUNTIME_ROLES["health"],
        "CREATE TABLE IF NOT EXISTS health.acl_probe_cross_write "
        "(key TEXT PRIMARY KEY, value JSONB NOT NULL)",
    )

    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role(
            db_url,
            _RUNTIME_ROLES["general"],
            "INSERT INTO health.acl_probe_cross_write (key, value)"
            " VALUES ('acl-probe-cross', '\"blocked\"'::jsonb)",
        )


def test_set_role_allows_public_table_writes(postgres_container):
    """SET ROLE butler_general_rw: write succeeds for each table in the public write matrix.

    Iterates all public tables guaranteed to exist after the core migration chain.
    Each test row is written under the runtime role; success proves the write grant
    is in effect.

    Note: qa_repo_config is only UPDATE-granted (not INSERT), so a seed row is
    pre-inserted via the admin connection before the role loop runs.
    """
    db_url = _create_db(postgres_container, _unique_db_name())
    _require_runtime_acl(db_url)

    # Pre-seed the qa_repo_config row that the role will UPDATE (no INSERT grant).
    seed_engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with seed_engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO public.qa_repo_config (repo_url)"
                    " VALUES ('https://example.com/acl-probe.git')"
                    " ON CONFLICT DO NOTHING"
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


def test_set_role_blocks_private_restore_drill_ledger_for_every_runtime_role(postgres_container):
    """Trusted bootstrap removes role-specific ledger grants from every runtime role.

    The canonical bootstrap intentionally grants runtime roles DML on shared
    ``public`` tables, including migration-created metadata.  The restore-drill
    ledger is the relevant protected authority boundary: ordinary runtime roles
    must not receive schema usage or ledger SELECT/INSERT. The test first
    reproduces the legacy role-specific grant escape, then runs the actual
    privileged bootstrap repair; ordinary assertions remain under the normal
    NOCREATEDB migration login's ``SET ROLE`` lifecycle.
    """
    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)
    _require_runtime_acl(db_url)
    assert all(_role_exists(db_url, role) for role in _RESTORE_DRILL_DENIED_ROLES)

    migration_user = urlparse(db_url).username
    assert migration_user is not None
    control_db_url = migration_bootstrap_db_url(postgres_container, db_name)
    control_engine = create_engine(control_db_url, isolation_level="AUTOCOMMIT")
    try:
        with control_engine.connect() as conn:
            for role in _RESTORE_DRILL_DENIED_ROLES:
                quoted_role = _quote_ident(role)
                conn.execute(text(f"GRANT USAGE ON SCHEMA restore_drill_executor TO {quoted_role}"))
                conn.execute(
                    text(
                        "GRANT SELECT, INSERT ON TABLE "
                        "restore_drill_executor.restore_drill_results "
                        f"TO {quoted_role}"
                    )
                )
    finally:
        control_engine.dispose()

    expected_legacy_grant = {
        "schema_usage": True,
        "ledger_select": True,
        "ledger_insert": True,
    }
    for role in _RESTORE_DRILL_DENIED_ROLES:
        assert _restore_drill_ledger_effective_privileges(db_url, role) == expected_legacy_grant

    _rerun_init_db_as_control(control_db_url, migration_user)

    expected_denial = {
        "schema_usage": False,
        "ledger_select": False,
        "ledger_insert": False,
    }
    for role in _RESTORE_DRILL_DENIED_ROLES:
        assert _restore_drill_ledger_effective_privileges(db_url, role) == expected_denial


def test_connector_writer_role_enforcement(postgres_container):
    """SET ROLE connector_writer: can write connectors schema and public.ingestion_events.

    Verifies that connector_writer cannot write to a butler runtime schema.
    """
    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)
    _require_runtime_acl(db_url)
    _require_connector_writer(db_url)

    session_role = "connector_probe"

    control_db_url = migration_bootstrap_db_url(postgres_container, db_name)
    setup_engine = create_engine(control_db_url, isolation_level="AUTOCOMMIT")
    try:
        with setup_engine.connect() as conn:
            conn.execute(
                text(f"CREATE ROLE {session_role} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT")
            )
            conn.execute(text(f"GRANT connector_writer TO {session_role} WITH SET TRUE"))
    finally:
        setup_engine.dispose()

    # connector_writer can INSERT into connectors schema tables.
    # connectors.filtered_events is created by core_007; we need to ensure
    # the partition exists.  Use the partition-ensuring function if available,
    # or insert a probe into the table directly.
    _execute_as_role_via_session_auth(
        control_db_url,
        session_role,
        "connector_writer",
        "SELECT connectors.connectors_filtered_events_ensure_partition(now())",
    )
    _execute_as_role_via_session_auth(
        control_db_url,
        session_role,
        "connector_writer",
        "INSERT INTO connectors.filtered_events"
        " (connector_type, endpoint_identity, external_message_id,"
        "  source_channel, sender_identity, filter_reason, full_payload)"
        " VALUES ('probe', 'ep-connector', 'ext-connector-1',"
        "  'acl', 'sender-1', 'acl-probe', '{}'::jsonb)",
    )

    # connector_writer can INSERT into public.ingestion_events (in the write matrix).
    _execute_as_role_via_session_auth(
        control_db_url,
        session_role,
        "connector_writer",
        "INSERT INTO public.ingestion_events"
        " (id, source_channel, source_provider, source_endpoint_identity,"
        "  external_event_id, dedupe_key, dedupe_strategy, ingestion_tier, policy_tier)"
        " VALUES (gen_random_uuid(), 'connector-acl', 'probe', 'ep-2',"
        "  'ext-2', 'dk-connector-probe-1', 'hash', 'full', 'standard')",
    )

    _execute_as_role(
        db_url,
        _RUNTIME_ROLES["general"],
        "CREATE TABLE IF NOT EXISTS general.acl_probe_connector_block "
        "(key TEXT PRIMARY KEY, value JSONB NOT NULL)",
    )

    # connector_writer cannot INSERT into a butler runtime schema.
    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role_via_session_auth(
            control_db_url,
            session_role,
            "connector_writer",
            "INSERT INTO general.acl_probe_connector_block (key, value)"
            " VALUES ('connector-probe-blocked', '\"blocked\"'::jsonb)",
        )


def test_role_fallback_when_absent(postgres_container, caplog):
    """Database.connect() with a non-existent role creates the pool without SET ROLE.

    The butler should operate normally (shared-user privileges) and log a warning
    rather than raising an exception.
    """
    db_url = _create_db(postgres_container, _unique_db_name())

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

    async def _exercise() -> tuple[int | None, bool]:
        pool = await db.connect()
        try:
            result = await pool.fetchval("SELECT 1")
            return result, db._role_verified
        finally:
            await db.close()

    with caplog.at_level(logging.WARNING, logger="butlers.db"):
        result, role_verified = asyncio.run(_exercise())

    # Pool was created; basic queries still work.
    assert result == 1
    # Role verification failed; _role_verified must be False.
    assert not role_verified
    # A warning about the missing role should have been logged.
    assert any(
        "butler_nonexistent_role_xyz" in record.message
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ), "Expected a warning about the absent role, got: " + str([r.message for r in caplog.records])


def test_role_reset_on_connection_return(postgres_container):
    """asyncpg RESET ALL + setup callback: role is re-applied on re-acquire.

    Acquires a connection from a SET ROLE pool, verifies the role is active,
    releases the connection back to the pool, then re-acquires and verifies the
    role is re-set by the setup callback (not lost after RESET ALL).
    """
    db_url = _create_db(postgres_container, _unique_db_name())
    _require_runtime_acl(db_url)

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


def test_role_reset_requires_normal_migration_login_membership(postgres_container):
    """Database SET ROLE fails when the normal login loses its membership."""
    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)
    _require_runtime_acl(db_url)

    role = _RUNTIME_ROLES["general"]
    parsed = urlparse(db_url)
    migration_user = parsed.username
    assert migration_user is not None

    login_engine = create_engine(db_url)
    try:
        with login_engine.connect() as conn:
            login_attributes = conn.execute(
                text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            ).one()
    finally:
        login_engine.dispose()

    assert login_attributes == (False, False, False)

    control_engine = create_engine(
        migration_bootstrap_db_url(postgres_container, db_name), isolation_level="AUTOCOMMIT"
    )
    try:
        with control_engine.connect() as conn:
            conn.execute(text(f"REVOKE {_quote_ident(role)} FROM {_quote_ident(migration_user)}"))
    finally:
        control_engine.dispose()

    from butlers.db import Database

    db = Database(
        db_name=parsed.path.lstrip("/"),
        role=role,
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        user=migration_user,
        password=parsed.password or "postgres",
        min_pool_size=1,
        max_pool_size=2,
    )

    async def _run() -> None:
        pool = await db.connect()
        try:
            with pytest.raises(
                asyncpg.exceptions.InsufficientPrivilegeError, match="permission denied"
            ):
                async with pool.acquire():
                    pass
        finally:
            await db.close()

    asyncio.run(_run())
